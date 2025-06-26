#!/usr/bin/env python
"""
Enhanced standalone script to run the Odoo MCP server
Includes comprehensive logging, error handling, and debugging features
"""
import sys
import os
import asyncio
import anyio
import logging
import datetime
import json
import traceback
from pathlib import Path

from mcp.server.stdio import stdio_server
from mcp.server.lowlevel import Server
import mcp.types as types

from odoo_mcp.server import mcp  # FastMCP instance from our code


class ColoredFormatter(logging.Formatter):
    """Custom formatter with colors for console output"""
    
    grey = "\x1b[38;21m"
    green = "\x1b[32m"
    yellow = "\x1b[33m"
    red = "\x1b[31m"
    bold_red = "\x1b[31;1m"
    reset = "\x1b[0m"
    
    FORMATS = {
        logging.DEBUG: grey + "%(asctime)s - %(name)s - %(levelname)s - %(message)s" + reset,
        logging.INFO: green + "%(asctime)s - %(name)s - %(levelname)s - %(message)s" + reset,
        logging.WARNING: yellow + "%(asctime)s - %(name)s - %(levelname)s - %(message)s" + reset,
        logging.ERROR: red + "%(asctime)s - %(name)s - %(levelname)s - %(message)s" + reset,
        logging.CRITICAL: bold_red + "%(asctime)s - %(name)s - %(levelname)s - %(message)s" + reset
    }
    
    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt, datefmt='%Y-%m-%d %H:%M:%S')
        return formatter.format(record)


def setup_logging():
    """Set up comprehensive logging to both console and file with rotation"""
    # Get log level from environment
    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
    
    # Create logs directory
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)
    
    # Generate log filename with timestamp
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(log_dir, f"mcp_server_{timestamp}.log")
    debug_log_file = os.path.join(log_dir, f"mcp_server_debug_{timestamp}.log")
    
    # Create symlinks to latest logs for easy access
    latest_log = os.path.join(log_dir, "mcp_server_latest.log")
    latest_debug_log = os.path.join(log_dir, "mcp_server_debug_latest.log")
    
    # Remove existing symlinks if they exist
    for link in [latest_log, latest_debug_log]:
        if os.path.islink(link) or os.path.exists(link):
            os.remove(link)
    
    # Configure root logger
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)  # Set to DEBUG to capture all
    
    # Console handler with color formatting
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(getattr(logging, log_level))
    
    # Use colored formatter if terminal supports it
    if sys.stderr.isatty():
        console_handler.setFormatter(ColoredFormatter())
    else:
        console_handler.setFormatter(
            logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        )
    
    # Main file handler (INFO and above)
    file_handler = logging.FileHandler(log_file, mode='a', encoding='utf-8')
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(
        logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    )
    
    # Debug file handler (all messages)
    debug_file_handler = logging.FileHandler(debug_log_file, mode='a', encoding='utf-8')
    debug_file_handler.setLevel(logging.DEBUG)
    debug_file_handler.setFormatter(
        logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s')
    )
    
    # Add handlers to logger
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    logger.addHandler(debug_file_handler)
    
    # Create symlinks to latest logs
    try:
        os.symlink(os.path.basename(log_file), latest_log)
        os.symlink(os.path.basename(debug_log_file), latest_debug_log)
    except:
        pass  # Symlinks might not work on all systems
    
    # Log rotation - keep only last 10 log files
    try:
        log_files = sorted([f for f in os.listdir(log_dir) if f.startswith("mcp_server_") and f.endswith(".log")])
        if len(log_files) > 20:  # Keep 10 regular + 10 debug logs
            for old_log in log_files[:-20]:
                os.remove(os.path.join(log_dir, old_log))
    except:
        pass
    
    return logger


def check_environment():
    """Check and validate environment configuration"""
    logger = logging.getLogger(__name__)
    
    # Check required environment variables
    required_vars = ["ODOO_URL", "ODOO_DB", "ODOO_USERNAME", "ODOO_PASSWORD"]
    missing_vars = []
    
    for var in required_vars:
        if var not in os.environ:
            missing_vars.append(var)
    
    if missing_vars:
        # Check for config file as fallback
        config_paths = [
            "./odoo_config.json",
            os.path.expanduser("~/.config/odoo/config.json"),
            os.path.expanduser("~/.odoo_config.json"),
        ]
        
        config_found = False
        for path in config_paths:
            if os.path.exists(path):
                logger.info(f"Found config file at: {path}")
                config_found = True
                break
        
        if not config_found:
            logger.warning(f"Missing environment variables: {', '.join(missing_vars)}")
            logger.warning("No config file found. Server may fail to connect to Odoo.")
    
    # Log current configuration (hiding sensitive data)
    logger.info("Current configuration:")
    for key, value in os.environ.items():
        if key.startswith("ODOO_"):
            if key == "ODOO_PASSWORD":
                logger.info(f"  {key}: {'*' * 8}")
            else:
                logger.info(f"  {key}: {value}")
        elif key in ["LOG_LEVEL", "HTTP_PROXY", "HTTPS_PROXY"]:
            logger.info(f"  {key}: {value}")


def create_diagnostics_file():
    """Create a diagnostics file with system information"""
    logger = logging.getLogger(__name__)
    
    try:
        diag_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
        diag_file = os.path.join(diag_dir, "diagnostics.json")
        
        diagnostics = {
            "timestamp": datetime.datetime.now().isoformat(),
            "python_version": sys.version,
            "platform": sys.platform,
            "working_directory": os.getcwd(),
            "environment": {
                k: v if k != "ODOO_PASSWORD" else "***" 
                for k, v in os.environ.items() 
                if k.startswith(("ODOO_", "LOG_", "HTTP_", "HTTPS_"))
            },
            "mcp_info": {
                "type": str(type(mcp)),
                "methods": [m for m in dir(mcp) if not m.startswith('_')],
            }
        }
        
        with open(diag_file, 'w') as f:
            json.dump(diagnostics, f, indent=2)
            
        logger.debug(f"Created diagnostics file: {diag_file}")
    except Exception as e:
        logger.error(f"Failed to create diagnostics file: {e}")


async def test_odoo_connection():
    """Test the Odoo connection before starting the server"""
    logger = logging.getLogger(__name__)
    
    try:
        # Try to import and initialize the Odoo client
        from odoo_mcp.odoo_client import get_odoo_client
        
        logger.info("Testing Odoo connection...")
        client = get_odoo_client()
        
        # Try a simple operation
        version = client._common.version()
        logger.info(f"✅ Successfully connected to Odoo {version.get('server_version', 'Unknown')}")
        
        # Test authentication
        if client.uid:
            logger.info(f"✅ Authenticated as user ID: {client.uid}")
            
            # Try to get user info
            try:
                user_info = client.execute_method('res.users', 'read', [client.uid], ['name', 'login'])
                if user_info:
                    logger.info(f"✅ Logged in as: {user_info[0].get('name', 'Unknown')} ({user_info[0].get('login', 'Unknown')})")
            except:
                pass
                
        return True
        
    except Exception as e:
        logger.error(f"❌ Failed to connect to Odoo: {str(e)}")
        logger.error("Please check your configuration and try again.")
        return False


def cleanup_old_logs():
    """Clean up old log files"""
    logger = logging.getLogger(__name__)
    
    try:
        log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
        if not os.path.exists(log_dir):
            return
            
        # Get all log files
        log_files = []
        for filename in os.listdir(log_dir):
            if filename.endswith('.log') and not filename.endswith('_latest.log'):
                filepath = os.path.join(log_dir, filename)
                if os.path.isfile(filepath):
                    log_files.append((filepath, os.path.getmtime(filepath)))
        
        # Sort by modification time
        log_files.sort(key=lambda x: x[1], reverse=True)
        
        # Keep only the most recent 20 files
        max_logs = int(os.environ.get("MAX_LOG_FILES", "20"))
        if len(log_files) > max_logs:
            for filepath, _ in log_files[max_logs:]:
                try:
                    os.remove(filepath)
                    logger.debug(f"Removed old log file: {os.path.basename(filepath)}")
                except:
                    pass
                    
    except Exception as e:
        logger.debug(f"Error during log cleanup: {e}")


def main() -> int:
    """
    Run the MCP server with enhanced error handling and logging
    """
    # Setup logging first
    logger = setup_logging()
    
    try:
        logger.info("=" * 60)
        logger.info("ODOO MCP SERVER STARTING")
        logger.info("=" * 60)
        logger.info(f"Python version: {sys.version}")
        logger.info(f"Server time: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        # Check environment
        check_environment()
        
        # Create diagnostics file
        create_diagnostics_file()
        
        # Clean up old logs
        cleanup_old_logs()
        
        # Test Odoo connection before starting server
        if os.environ.get("SKIP_CONNECTION_TEST", "").lower() != "true":
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            connection_ok = loop.run_until_complete(test_odoo_connection())
            loop.close()
            
            if not connection_ok:
                logger.error("Failed to establish Odoo connection. Server startup aborted.")
                logger.error("Set SKIP_CONNECTION_TEST=true to skip this check.")
                return 1
        else:
            logger.warning("Skipping connection test (SKIP_CONNECTION_TEST=true)")
        
        logger.info(f"MCP object type: {type(mcp)}")
        logger.info("Available methods: " + ", ".join([m for m in dir(mcp) if not m.startswith('_')]))
        
        # Run server in stdio mode
        async def arun():
            logger.info("Starting Odoo MCP server with stdio transport...")
            
            try:
                async with stdio_server() as streams:
                    logger.info("✅ Stdio server initialized successfully")
                    logger.info("Running MCP server... (Press Ctrl+C to stop)")
                    
                    # Add periodic health check
                    async def health_check():
                        while True:
                            await asyncio.sleep(300)  # Every 5 minutes
                            logger.debug("Health check: Server is running")
                    
                    # Start health check task
                    health_task = asyncio.create_task(health_check())
                    
                    try:
                        await mcp._mcp_server.run(
                            streams[0], 
                            streams[1], 
                            mcp._mcp_server.create_initialization_options()
                        )
                    finally:
                        health_task.cancel()
                        
            except Exception as e:
                logger.error(f"Error in stdio server: {e}")
                logger.error(traceback.format_exc())
                raise
        
        # Run server
        logger.info("Starting async event loop...")
        anyio.run(arun)
        
        logger.info("MCP server stopped normally")
        return 0
        
    except KeyboardInterrupt:
        logger.info("Server stopped by user (Ctrl+C)")
        return 0
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        logger.error("Exception traceback:")
        logger.error(traceback.format_exc())
        
        # Write crash report
        try:
            crash_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
            crash_file = os.path.join(crash_dir, f"crash_report_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
            
            with open(crash_file, 'w') as f:
                f.write(f"Crash Report - {datetime.datetime.now()}\n")
                f.write("=" * 60 + "\n\n")
                f.write(f"Error: {str(e)}\n\n")
                f.write("Traceback:\n")
                f.write(traceback.format_exc())
                f.write("\n\nEnvironment:\n")
                for key, value in os.environ.items():
                    if key.startswith(("ODOO_", "LOG_", "HTTP_")):
                        if key == "ODOO_PASSWORD":
                            f.write(f"{key}: ***\n")
                        else:
                            f.write(f"{key}: {value}\n")
                            
            logger.error(f"Crash report written to: {crash_file}")
        except:
            pass
            
        return 1
    finally:
        logger.info("=" * 60)
        logger.info("ODOO MCP SERVER SHUTDOWN COMPLETE")
        logger.info("=" * 60)


if __name__ == "__main__":
    sys.exit(main())

"""
Enhanced MCP server for Odoo development
Provides comprehensive tools for both Odoo database interaction AND local module file management
"""

import json
import logging
import os
import sys
import re
import ast
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, AsyncIterator, Dict, List, Optional, Union, cast
from pathlib import Path
import xml.etree.ElementTree as ET
from xml.dom import minidom

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field

from .odoo_client import OdooClient, get_odoo_client


# Configure logging
def setup_logging():
    """Setup comprehensive logging for debugging"""
    log_level = os.environ.get("LOG_LEVEL", "INFO")
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stderr),
            logging.FileHandler('odoo_mcp.log', mode='a')
        ]
    )
    return logging.getLogger(__name__)


logger = setup_logging()


@dataclass
class AppContext:
    """Application context for the MCP server"""
    odoo: OdooClient
    modules_path: str  # Path to custom modules directory


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    """
    Application lifespan for initialization and cleanup
    """
    logger.info("Initializing Combined Odoo MCP server...")
    
    try:
        # Initialize Odoo client
        odoo_client = get_odoo_client()
        logger.info(f"Successfully connected to Odoo at {odoo_client.url}")
        
        # Get modules path from environment or use default
        modules_path = os.environ.get("ODOO_MODULES_PATH", "./custom_modules")
        modules_path = os.path.abspath(modules_path)
        
        if not os.path.exists(modules_path):
            logger.warning(f"Modules path does not exist: {modules_path}")
            logger.info(f"Creating modules directory: {modules_path}")
            os.makedirs(modules_path, exist_ok=True)
        else:
            logger.info(f"Using modules path: {modules_path}")
            
        yield AppContext(odoo=odoo_client, modules_path=modules_path)
    except Exception as e:
        logger.error(f"Failed to initialize: {str(e)}")
        raise
    finally:
        logger.info("Shutting down Combined Odoo MCP server...")


# Create MCP server
mcp = FastMCP(
    "Combined Odoo Development MCP Server",
    description="Comprehensive MCP Server for Odoo development with both database interaction and file system management",
    dependencies=["requests"],
    lifespan=app_lifespan,
)


# ----- Pydantic models for type safety -----

class RecordData(BaseModel):
    """Data for creating or updating records"""
    fields: Dict[str, Any] = Field(description="Field values for the record")


class CreateRecordResponse(BaseModel):
    """Response for record creation"""
    success: bool
    record_id: Optional[int] = None
    error: Optional[str] = None


class UpdateRecordResponse(BaseModel):
    """Response for record update"""
    success: bool
    updated: Optional[bool] = None
    error: Optional[str] = None


class DeleteRecordResponse(BaseModel):
    """Response for record deletion"""
    success: bool
    deleted: Optional[bool] = None
    error: Optional[str] = None


class FieldDefinition(BaseModel):
    """Field definition for model fields"""
    name: str
    field_type: str = Field(alias="type", description="Field type (char, text, many2one, etc.)")
    string: str = Field(description="Field label")
    required: bool = False
    readonly: bool = False
    help: Optional[str] = None
    size: Optional[int] = None
    relation: Optional[str] = None


class ViewDefinition(BaseModel):
    """View definition"""
    name: str
    model: str
    view_type: str = Field(description="View type (form, tree, kanban, etc.)")
    arch: str = Field(description="View architecture (XML)")
    priority: int = 16
    inherit_id: Optional[int] = None


class ModuleInfo(BaseModel):
    """Module information"""
    name: str
    state: str
    installed: bool
    dependencies: List[str] = Field(default_factory=list)


class FileContent(BaseModel):
    """File content model"""
    path: str = Field(description="File path relative to module")
    content: str = Field(description="File content")
    encoding: str = Field(default="utf-8", description="File encoding")


class ModuleManifest(BaseModel):
    """Odoo module manifest data"""
    name: str = Field(description="Module technical name")
    version: str = Field(default="1.0.0", description="Module version")
    category: str = Field(default="Uncategorized", description="Module category")
    summary: str = Field(default="", description="Short module summary")
    description: str = Field(default="", description="Module description")
    author: str = Field(default="Your Company", description="Module author")
    website: str = Field(default="", description="Author website")
    depends: List[str] = Field(default_factory=lambda: ["base"], description="Module dependencies")
    data: List[str] = Field(default_factory=list, description="Data files to load")
    demo: List[str] = Field(default_factory=list, description="Demo data files")
    installable: bool = Field(default=True, description="Whether module is installable")
    application: bool = Field(default=False, description="Whether module is an application")
    auto_install: bool = Field(default=False, description="Whether to auto-install")


class PythonModel(BaseModel):
    """Python model definition"""
    name: str = Field(description="Model name (e.g., 'my.model')")
    inherit: Optional[str] = Field(default=None, description="Model to inherit from")
    description: str = Field(default="", description="Model description")
    fields: Dict[str, Dict[str, Any]] = Field(default_factory=dict, description="Field definitions")


class XMLView(BaseModel):
    """XML view definition"""
    id: str = Field(description="View ID")
    name: str = Field(description="View name")
    model: str = Field(description="Model name")
    type: str = Field(default="form", description="View type (form, tree, kanban, etc.)")
    priority: int = Field(default=16, description="View priority")
    arch: str = Field(description="View architecture (XML)")
    inherit_id: Optional[str] = Field(default=None, description="Parent view to inherit")


# ----- Database Resources -----

@mcp.resource(
    "odoo://database/models",
    description="List all available models in the Odoo database with details"
)
def get_database_models() -> str:
    """Lists all available models in the Odoo database"""
    odoo_client = get_odoo_client()
    try:
        logger.debug("Fetching all models from database...")
        models = odoo_client.get_models()
        logger.info(f"Retrieved {len(models.get('model_names', []))} models from database")
        return json.dumps(models, indent=2)
    except Exception as e:
        logger.error(f"Error fetching models from database: {str(e)}")
        return json.dumps({"error": str(e)}, indent=2)


@mcp.resource(
    "odoo://database/model/{model_name}",
    description="Get detailed information about a specific model from database including fields"
)
def get_database_model_info(model_name: str) -> str:
    """
    Get information about a specific model from database
    
    Parameters:
        model_name: Name of the Odoo model (e.g., 'res.partner')
    """
    odoo_client = get_odoo_client()
    try:
        logger.debug(f"Fetching database info for model: {model_name}")
        
        # Get model info
        model_info = odoo_client.get_model_info(model_name)
        
        # Get field definitions
        fields = odoo_client.get_model_fields(model_name)
        model_info["fields"] = fields
        
        # Get access rights info
        try:
            access_info = odoo_client.execute_method(
                'ir.model.access', 'check', model_name, 'read', raise_exception=False
            )
            model_info["access_rights"] = {
                "read": access_info,
                "write": odoo_client.execute_method(
                    'ir.model.access', 'check', model_name, 'write', raise_exception=False
                ),
                "create": odoo_client.execute_method(
                    'ir.model.access', 'check', model_name, 'create', raise_exception=False
                ),
                "unlink": odoo_client.execute_method(
                    'ir.model.access', 'check', model_name, 'unlink', raise_exception=False
                ),
            }
        except:
            model_info["access_rights"] = "Unable to fetch"
            
        logger.info(f"Successfully retrieved database info for model {model_name}")
        return json.dumps(model_info, indent=2)
    except Exception as e:
        logger.error(f"Error fetching database model info for {model_name}: {str(e)}")
        return json.dumps({"error": str(e)}, indent=2)


@mcp.resource(
    "odoo://database/views/{model_name}",
    description="Get all views for a specific model from database"
)
def get_database_model_views(model_name: str) -> str:
    """Get all views defined for a model from database"""
    odoo_client = get_odoo_client()
    try:
        logger.debug(f"Fetching database views for model: {model_name}")
        
        views = odoo_client.execute_method(
            'ir.ui.view',
            'search_read',
            [('model', '=', model_name)],
            {
                'fields': ['name', 'type', 'arch', 'priority', 'inherit_id'],
                'order': 'priority,id'
            }
        )
        
        logger.info(f"Found {len(views)} views in database for model {model_name}")
        return json.dumps(views, indent=2)
    except Exception as e:
        logger.error(f"Error fetching database views for {model_name}: {str(e)}")
        return json.dumps({"error": str(e)}, indent=2)


@mcp.resource(
    "odoo://database/modules",
    description="List all modules with their installation status from database"
)
def get_database_modules() -> str:
    """Get all modules and their status from database"""
    odoo_client = get_odoo_client()
    try:
        logger.debug("Fetching all modules from database...")
        
        modules = odoo_client.execute_method(
            'ir.module.module',
            'search_read',
            [],
            {
                'fields': ['name', 'state', 'shortdesc', 'author', 'installed_version'],
                'order': 'name'
            }
        )
        
        logger.info(f"Found {len(modules)} modules in database")
        return json.dumps(modules, indent=2)
    except Exception as e:
        logger.error(f"Error fetching modules from database: {str(e)}")
        return json.dumps({"error": str(e)}, indent=2)


# ----- File System Resources -----

@mcp.resource(
    "odoo://filesystem/modules",
    description="List all custom modules in the local modules directory"
)
def list_filesystem_modules() -> str:
    """List all modules in the custom modules directory"""
    # Access modules path from environment since resources don't have context
    modules_path = os.environ.get("ODOO_MODULES_PATH", "./custom_modules")
    modules_path = os.path.abspath(modules_path)
    
    try:
        modules = []
        
        for item in os.listdir(modules_path):
            item_path = os.path.join(modules_path, item)
            if os.path.isdir(item_path):
                # Check if it's a valid Odoo module
                manifest_path = os.path.join(item_path, "__manifest__.py")
                if os.path.exists(manifest_path):
                    try:
                        # Read manifest
                        with open(manifest_path, 'r', encoding='utf-8') as f:
                            content = f.read()
                            # Safe evaluation of manifest
                            manifest = ast.literal_eval(content)
                            
                        modules.append({
                            "name": item,
                            "display_name": manifest.get("name", item),
                            "version": manifest.get("version", ""),
                            "summary": manifest.get("summary", ""),
                            "installable": manifest.get("installable", True),
                            "path": item_path
                        })
                    except Exception as e:
                        logger.warning(f"Error reading manifest for {item}: {e}")
                        modules.append({
                            "name": item,
                            "error": str(e),
                            "path": item_path
                        })
                        
        return json.dumps({
            "modules_path": modules_path,
            "modules": modules,
            "count": len(modules)
        }, indent=2)
        
    except Exception as e:
        logger.error(f"Error listing filesystem modules: {str(e)}")
        return json.dumps({"error": str(e)}, indent=2)


@mcp.resource(
    "odoo://filesystem/module/{module_name}/files",
    description="List all files in a specific module"
)
def list_module_files(module_name: str) -> str:
    """List all files in a module with their structure"""
    modules_path = os.environ.get("ODOO_MODULES_PATH", "./custom_modules")
    modules_path = os.path.abspath(modules_path)
    module_path = os.path.join(modules_path, module_name)
    
    try:
        if not os.path.exists(module_path):
            return json.dumps({"error": f"Module {module_name} not found"}, indent=2)
            
        files = []
        
        for root, dirs, filenames in os.walk(module_path):
            # Skip __pycache__ directories
            dirs[:] = [d for d in dirs if d != '__pycache__']
            
            for filename in filenames:
                # Skip compiled Python files
                if filename.endswith('.pyc'):
                    continue
                    
                file_path = os.path.join(root, filename)
                relative_path = os.path.relpath(file_path, module_path)
                
                files.append({
                    "path": relative_path,
                    "type": get_file_type(filename),
                    "size": os.path.getsize(file_path),
                    "modified": datetime.fromtimestamp(os.path.getmtime(file_path)).isoformat()
                })
                
        return json.dumps({
            "module": module_name,
            "path": module_path,
            "files": files,
            "count": len(files)
        }, indent=2)
        
    except Exception as e:
        logger.error(f"Error listing files for module {module_name}: {str(e)}")
        return json.dumps({"error": str(e)}, indent=2)


@mcp.resource(
    "odoo://filesystem/module/{module_name}/file/{file_path}",
    description="Read a specific file from a module"
)
def read_module_file(module_name: str, file_path: str) -> str:
    """Read content of a specific file in a module"""
    modules_path = os.environ.get("ODOO_MODULES_PATH", "./custom_modules")
    modules_path = os.path.abspath(modules_path)
    full_path = os.path.join(modules_path, module_name, file_path)
    
    try:
        # Security check - ensure path doesn't escape module directory
        real_path = os.path.realpath(full_path)
        module_real_path = os.path.realpath(os.path.join(modules_path, module_name))
        
        if not real_path.startswith(module_real_path):
            return json.dumps({"error": "Invalid file path"}, indent=2)
            
        if not os.path.exists(full_path):
            return json.dumps({"error": f"File not found: {file_path}"}, indent=2)
            
        # Determine encoding
        encoding = 'utf-8'
        if file_path.endswith(('.jpg', '.jpeg', '.png', '.gif', '.pdf', '.ico')):
            # Binary files
            with open(full_path, 'rb') as f:
                import base64
                content = base64.b64encode(f.read()).decode('ascii')
                encoding = 'base64'
        else:
            # Text files
            with open(full_path, 'r', encoding='utf-8') as f:
                content = f.read()
                
        return json.dumps({
            "module": module_name,
            "path": file_path,
            "content": content,
            "encoding": encoding,
            "type": get_file_type(file_path),
            "size": os.path.getsize(full_path)
        }, indent=2)
        
    except Exception as e:
        logger.error(f"Error reading file {file_path} from module {module_name}: {str(e)}")
        return json.dumps({"error": str(e)}, indent=2)


# ----- Database Tools -----

@mcp.tool(description="Create a new record in any Odoo model")
def create_record(
    ctx: Context,
    model: str,
    values: Dict[str, Any]
) -> CreateRecordResponse:
    """
    Create a new record in the specified model
    
    Parameters:
        model: The model name (e.g., 'res.partner')
        values: Dictionary of field values for the new record
        
    Example:
        create_record("res.partner", {"name": "John Doe", "email": "john@example.com"})
    """
    odoo = ctx.request_context.lifespan_context.odoo
    
    try:
        logger.info(f"Creating record in {model} with values: {values}")
        
        # Execute create method
        record_id = odoo.execute_method(model, 'create', values)
        
        logger.info(f"Successfully created record with ID {record_id} in {model}")
        return CreateRecordResponse(success=True, record_id=record_id)
        
    except Exception as e:
        logger.error(f"Error creating record in {model}: {str(e)}")
        return CreateRecordResponse(success=False, error=str(e))


@mcp.tool(description="Update existing records in any Odoo model")
def update_record(
    ctx: Context,
    model: str,
    record_ids: Union[int, List[int]],
    values: Dict[str, Any]
) -> UpdateRecordResponse:
    """
    Update existing records in the specified model
    
    Parameters:
        model: The model name (e.g., 'res.partner')
        record_ids: Single ID or list of IDs to update
        values: Dictionary of field values to update
        
    Example:
        update_record("res.partner", 1, {"phone": "+1234567890"})
    """
    odoo = ctx.request_context.lifespan_context.odoo
    
    try:
        # Ensure record_ids is a list
        if isinstance(record_ids, int):
            record_ids = [record_ids]
            
        logger.info(f"Updating records {record_ids} in {model} with values: {values}")
        
        # Execute write method
        result = odoo.execute_method(model, 'write', record_ids, values)
        
        logger.info(f"Successfully updated records {record_ids} in {model}")
        return UpdateRecordResponse(success=True, updated=result)
        
    except Exception as e:
        logger.error(f"Error updating records in {model}: {str(e)}")
        return UpdateRecordResponse(success=False, error=str(e))


@mcp.tool(description="Delete records from any Odoo model")
def delete_record(
    ctx: Context,
    model: str,
    record_ids: Union[int, List[int]]
) -> DeleteRecordResponse:
    """
    Delete records from the specified model
    
    Parameters:
        model: The model name (e.g., 'res.partner')
        record_ids: Single ID or list of IDs to delete
        
    Example:
        delete_record("res.partner", [1, 2, 3])
    """
    odoo = ctx.request_context.lifespan_context.odoo
    
    try:
        # Ensure record_ids is a list
        if isinstance(record_ids, int):
            record_ids = [record_ids]
            
        logger.info(f"Deleting records {record_ids} from {model}")
        
        # Execute unlink method
        result = odoo.execute_method(model, 'unlink', record_ids)
        
        logger.info(f"Successfully deleted records {record_ids} from {model}")
        return DeleteRecordResponse(success=True, deleted=result)
        
    except Exception as e:
        logger.error(f"Error deleting records from {model}: {str(e)}")
        return DeleteRecordResponse(success=False, error=str(e))


@mcp.tool(description="Search for records with advanced domain queries")
def search_records(
    ctx: Context,
    model: str,
    domain: Optional[Union[List, Dict, str]] = None,
    fields: Optional[List[str]] = None,
    limit: int = 80,
    offset: int = 0,
    order: Optional[str] = None
) -> Dict[str, Any]:
    """
    Search for records with advanced filtering
    
    Parameters:
        model: The model name (e.g., 'res.partner')
        domain: Search domain (can be list, dict, or JSON string)
        fields: List of fields to return
        limit: Maximum records to return
        offset: Number of records to skip
        order: Sort order (e.g., 'name desc, id')
        
    Examples:
        search_records("res.partner", [["is_company", "=", True]], ["name", "email"])
        search_records("product.product", {"conditions": [{"field": "type", "operator": "=", "value": "product"}]})
    """
    odoo = ctx.request_context.lifespan_context.odoo
    
    try:
        # Normalize domain
        domain_list = []
        
        if domain is None:
            domain_list = []
        elif isinstance(domain, dict):
            # Object format with conditions
            if "conditions" in domain:
                conditions = domain.get("conditions", [])
                for cond in conditions:
                    if isinstance(cond, dict) and all(k in cond for k in ["field", "operator", "value"]):
                        domain_list.append([cond["field"], cond["operator"], cond["value"]])
        elif isinstance(domain, list):
            domain_list = domain
        elif isinstance(domain, str):
            # Try to parse as JSON
            try:
                parsed = json.loads(domain)
                if isinstance(parsed, dict) and "conditions" in parsed:
                    conditions = parsed.get("conditions", [])
                    for cond in conditions:
                        if isinstance(cond, dict) and all(k in cond for k in ["field", "operator", "value"]):
                            domain_list.append([cond["field"], cond["operator"], cond["value"]])
                elif isinstance(parsed, list):
                    domain_list = parsed
            except json.JSONDecodeError:
                logger.warning(f"Could not parse domain string: {domain}")
                
        logger.info(f"Searching {model} with domain: {domain_list}, limit: {limit}")
        
        # Execute search_read
        results = odoo.search_read(
            model_name=model,
            domain=domain_list,
            fields=fields,
            offset=offset,
            limit=limit,
            order=order
        )
        
        logger.info(f"Found {len(results)} records in {model}")
        
        return {
            "success": True,
            "count": len(results),
            "records": results
        }
        
    except Exception as e:
        logger.error(f"Error searching records in {model}: {str(e)}")
        return {
            "success": False,
            "error": str(e),
            "records": []
        }


@mcp.tool(description="Get field information for a model to understand data structure")
def get_fields_info(
    ctx: Context,
    model: str,
    field_names: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Get detailed field information for a model
    
    Parameters:
        model: The model name (e.g., 'res.partner')
        field_names: Optional list of specific field names to get info for
        
    Returns detailed information about fields including type, required, readonly, etc.
    """
    odoo = ctx.request_context.lifespan_context.odoo
    
    try:
        logger.info(f"Getting field info for model {model}")
        
        # Get all fields or specific fields
        if field_names:
            fields = odoo.execute_method(model, 'fields_get', field_names)
        else:
            fields = odoo.execute_method(model, 'fields_get')
            
        # Process fields to make them more readable
        processed_fields = {}
        for fname, finfo in fields.items():
            processed_fields[fname] = {
                "type": finfo.get("type"),
                "string": finfo.get("string"),
                "required": finfo.get("required", False),
                "readonly": finfo.get("readonly", False),
                "help": finfo.get("help"),
                "relation": finfo.get("relation"),  # For many2one, one2many, many2many
                "selection": finfo.get("selection"),  # For selection fields
                "size": finfo.get("size"),  # For char fields
                "digits": finfo.get("digits"),  # For float fields
                "domain": finfo.get("domain"),  # For relational fields
            }
            
        logger.info(f"Retrieved info for {len(processed_fields)} fields")
        
        return {
            "success": True,
            "model": model,
            "fields": processed_fields
        }
        
    except Exception as e:
        logger.error(f"Error getting field info for {model}: {str(e)}")
        return {
            "success": False,
            "error": str(e),
            "fields": {}
        }


@mcp.tool(description="Install, update, or uninstall Odoo modules")
def manage_module(
    ctx: Context,
    module_name: str,
    action: str
) -> Dict[str, Any]:
    """
    Manage Odoo modules (install, upgrade, uninstall)
    
    Parameters:
        module_name: Technical name of the module (e.g., 'sale', 'purchase')
        action: Action to perform ('install', 'upgrade', 'uninstall')
        
    Note: This may take time and requires appropriate permissions
    """
    odoo = ctx.request_context.lifespan_context.odoo
    
    try:
        logger.info(f"Managing module {module_name}: {action}")
        
        # Find the module
        module = odoo.execute_method(
            'ir.module.module',
            'search_read',
            [('name', '=', module_name)],
            {'fields': ['id', 'state'], 'limit': 1}
        )
        
        if not module:
            return {
                "success": False,
                "error": f"Module {module_name} not found"
            }
            
        module_id = module[0]['id']
        current_state = module[0]['state']
        
        logger.info(f"Module {module_name} current state: {current_state}")
        
        # Perform action based on request
        if action == 'install':
            if current_state == 'installed':
                return {
                    "success": True,
                    "message": f"Module {module_name} is already installed"
                }
            odoo.execute_method('ir.module.module', 'button_install', [module_id])
            
        elif action == 'upgrade':
            if current_state != 'installed':
                return {
                    "success": False,
                    "error": f"Module {module_name} must be installed before upgrading"
                }
            odoo.execute_method('ir.module.module', 'button_upgrade', [module_id])
            
        elif action == 'uninstall':
            if current_state != 'installed':
                return {
                    "success": False,
                    "error": f"Module {module_name} is not installed"
                }
            odoo.execute_method('ir.module.module', 'button_uninstall', [module_id])
            
        else:
            return {
                "success": False,
                "error": f"Invalid action: {action}. Use 'install', 'upgrade', or 'uninstall'"
            }
            
        logger.info(f"Successfully initiated {action} for module {module_name}")
        
        return {
            "success": True,
            "message": f"Module {module_name} {action} initiated successfully",
            "note": "Module actions may take time to complete. Check module state after a few moments."
        }
        
    except Exception as e:
        logger.error(f"Error managing module: {str(e)}")
        return {
            "success": False,
            "error": str(e)
        }


@mcp.tool(description="Get detailed system information and configuration")
def get_system_info(ctx: Context) -> Dict[str, Any]:
    """
    Get comprehensive system information including version, modules, users, etc.
    """
    odoo = ctx.request_context.lifespan_context.odoo
    
    try:
        logger.info("Getting system information")
        
        info = {}
        
        # Get version info
        try:
            common = odoo._common
            info['version'] = common.version()
        except:
            info['version'] = "Unable to fetch"
            
        # Get database info
        try:
            info['database'] = {
                'name': odoo.db,
                'uuid': odoo.execute_method('ir.config_parameter', 'get_param', 'database.uuid')
            }
        except:
            info['database'] = {'name': odoo.db}
            
        # Get user info
        try:
            user_info = odoo.execute_method(
                'res.users',
                'read',
                [odoo.uid],
                ['name', 'login', 'lang', 'tz', 'company_id']
            )
            info['current_user'] = user_info[0] if user_info else {}
        except:
            info['current_user'] = {'id': odoo.uid}
            
        # Get installed modules count
        try:
            module_count = odoo.execute_method(
                'ir.module.module',
                'search_count',
                [('state', '=', 'installed')]
            )
            info['installed_modules_count'] = module_count
        except:
            info['installed_modules_count'] = "Unable to fetch"
            
        # Get company info
        try:
            company_info = odoo.execute_method(
                'res.company',
                'search_read',
                [],
                {'fields': ['name', 'email', 'phone'], 'limit': 5}
            )
            info['companies'] = company_info
        except:
            info['companies'] = []
            
        logger.info("Successfully retrieved system information")
        
        return {
            "success": True,
            "system_info": info
        }
        
    except Exception as e:
        logger.error(f"Error getting system info: {str(e)}")
        return {
            "success": False,
            "error": str(e)
        }


@mcp.tool(description="Execute any method on an Odoo model (advanced)")
def execute_method(
    ctx: Context,
    model: str,
    method: str,
    args: List = None,
    kwargs: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Execute any method on an Odoo model (for advanced users)
    
    Parameters:
        model: The model name (e.g., 'res.partner')
        method: Method name to execute
        args: Positional arguments
        kwargs: Keyword arguments
    """
    odoo = ctx.request_context.lifespan_context.odoo
    
    try:
        args = args or []
        kwargs = kwargs or {}
        
        logger.info(f"Executing {model}.{method} with args={args}, kwargs={kwargs}")
        
        result = odoo.execute_method(model, method, *args, **kwargs)
        
        logger.info(f"Method {method} executed successfully")
        
        return {"success": True, "result": result}
    except Exception as e:
        logger.error(f"Error executing method {method} on {model}: {str(e)}")
        return {"success": False, "error": str(e)}


# Keep the original tools for backward compatibility
@mcp.tool(description="Search for employees by name")
def search_employee(
    ctx: Context,
    name: str,
    limit: int = 20,
) -> Dict[str, Any]:
    """
    Search for employees by name
    
    Parameters:
        name: The name (or part of the name) to search for
        limit: Maximum number of results
    """
    odoo = ctx.request_context.lifespan_context.odoo
    
    try:
        logger.info(f"Searching for employees with name like '{name}'")
        
        result = odoo.execute_method(
            'hr.employee',
            'name_search',
            name=name,
            limit=limit
        )
        
        employees = [{"id": item[0], "name": item[1]} for item in result]
        
        logger.info(f"Found {len(employees)} employees")
        
        return {
            "success": True,
            "result": employees
        }
    except Exception as e:
        logger.error(f"Error searching employees: {str(e)}")
        return {
            "success": False,
            "error": str(e)
        }


# ----- File System Tools -----

@mcp.tool(description="Create a new Odoo module with basic structure")
def create_module(
    ctx: Context,
    name: str,
    display_name: str,
    summary: str = "",
    author: str = "Your Company",
    category: str = "Uncategorized",
    depends: List[str] = None,
    is_application: bool = False
) -> Dict[str, Any]:
    """
    Create a new Odoo module with standard directory structure
    
    Parameters:
        name: Technical name of the module (e.g., 'my_module')
        display_name: Human-readable name
        summary: Short description
        author: Module author
        category: Module category
        depends: List of dependencies (default: ['base'])
        is_application: Whether this is a full application
    """
    modules_path = ctx.request_context.lifespan_context.modules_path
    module_path = os.path.join(modules_path, name)
    
    try:
        # Validate module name
        if not re.match(r'^[a-z][a-z0-9_]*$', name):
            return {
                "success": False,
                "error": "Module name must start with lowercase letter and contain only lowercase letters, numbers, and underscores"
            }
            
        if os.path.exists(module_path):
            return {
                "success": False,
                "error": f"Module {name} already exists"
            }
            
        logger.info(f"Creating module {name} at {module_path}")
        
        # Create module directory structure
        os.makedirs(module_path)
        os.makedirs(os.path.join(module_path, "models"))
        os.makedirs(os.path.join(module_path, "views"))
        os.makedirs(os.path.join(module_path, "security"))
        os.makedirs(os.path.join(module_path, "data"))
        os.makedirs(os.path.join(module_path, "static/description"))
        
        # Create __manifest__.py
        manifest = {
            'name': display_name,
            'version': '1.0.0',
            'category': category,
            'summary': summary,
            'description': f"""
{display_name}
{'=' * len(display_name)}

{summary or 'Module description goes here...'}
""",
            'author': author,
            'depends': depends or ['base'],
            'data': [
                'security/ir.model.access.csv',
                # Views will be added here
            ],
            'demo': [],
            'installable': True,
            'application': is_application,
            'auto_install': False,
        }
        
        with open(os.path.join(module_path, "__manifest__.py"), 'w') as f:
            f.write("# -*- coding: utf-8 -*-\n")
            f.write(repr(manifest))
            
        # Create __init__.py files
        with open(os.path.join(module_path, "__init__.py"), 'w') as f:
            f.write("# -*- coding: utf-8 -*-\n\nfrom . import models\n")
            
        with open(os.path.join(module_path, "models", "__init__.py"), 'w') as f:
            f.write("# -*- coding: utf-8 -*-\n\n")
            
        # Create basic security file
        with open(os.path.join(module_path, "security", "ir.model.access.csv"), 'w') as f:
            f.write("id,name,model_id:id,group_id:id,perm_read,perm_write,perm_create,perm_unlink\n")
            
        # Create module icon placeholder
        icon_path = os.path.join(module_path, "static/description/icon.png")
        with open(icon_path, 'w') as f:
            f.write("<!-- Add your module icon here -->")
            
        logger.info(f"Successfully created module {name}")
        
        return {
            "success": True,
            "module_name": name,
            "module_path": module_path,
            "message": f"Module {name} created successfully",
            "structure": {
                "manifest": "__manifest__.py",
                "models": "models/",
                "views": "views/",
                "security": "security/",
                "data": "data/",
                "static": "static/description/"
            }
        }
        
    except Exception as e:
        logger.error(f"Error creating module {name}: {str(e)}")
        return {
            "success": False,
            "error": str(e)
        }


@mcp.tool(description="Create or update a file in an Odoo module")
def write_module_file(
    ctx: Context,
    module_name: str,
    file_path: str,
    content: str,
    create_directories: bool = True
) -> Dict[str, Any]:
    """
    Write content to a file in a module
    
    Parameters:
        module_name: Name of the module
        file_path: Path to file relative to module root
        content: File content
        create_directories: Whether to create parent directories if they don't exist
    """
    modules_path = ctx.request_context.lifespan_context.modules_path
    full_path = os.path.join(modules_path, module_name, file_path)
    
    try:
        # Security check
        real_path = os.path.realpath(full_path)
        module_real_path = os.path.realpath(os.path.join(modules_path, module_name))
        
        if not real_path.startswith(module_real_path):
            return {
                "success": False,
                "error": "Invalid file path - attempting to write outside module directory"
            }
            
        # Create directories if needed
        if create_directories:
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            
        # Backup existing file if it exists
        backup_path = None
        if os.path.exists(full_path):
            backup_path = full_path + '.backup.' + datetime.now().strftime('%Y%m%d_%H%M%S')
            with open(full_path, 'r', encoding='utf-8') as f:
                backup_content = f.read()
            with open(backup_path, 'w', encoding='utf-8') as f:
                f.write(backup_content)
                
        # Write new content
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(content)
            
        logger.info(f"Successfully wrote file {file_path} in module {module_name}")
        
        return {
            "success": True,
            "module": module_name,
            "file_path": file_path,
            "full_path": full_path,
            "backup_path": backup_path,
            "size": len(content)
        }
        
    except Exception as e:
        logger.error(f"Error writing file {file_path} in module {module_name}: {str(e)}")
        return {
            "success": False,
            "error": str(e)
        }


@mcp.tool(description="Create a Python model file in an Odoo module")
def create_model_file(
    ctx: Context,
    module_name: str,
    model_name: str,
    model_description: str,
    inherit_from: Optional[str] = None,
    fields: Optional[Dict[str, Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """
    Create a Python model file with proper Odoo structure
    
    Parameters:
        module_name: Module name
        model_name: Technical model name (e.g., 'my.model')
        model_description: Human-readable model name
        inherit_from: Model to inherit from (optional)
        fields: Dictionary of field definitions
        
    Example fields:
        {
            "name": {"type": "Char", "string": "Name", "required": True},
            "date": {"type": "Date", "string": "Date", "default": "fields.Date.today"},
            "partner_id": {"type": "Many2one", "comodel": "res.partner", "string": "Partner"}
        }
    """
    try:
        # Generate filename from model name
        filename = model_name.replace('.', '_') + '.py'
        file_path = os.path.join('models', filename)
        
        # Generate model code
        code_lines = [
            "# -*- coding: utf-8 -*-",
            "",
            "from odoo import models, fields, api",
            "",
            ""
        ]
        
        # Class definition
        class_name = ''.join(word.capitalize() for word in model_name.split('.'))
        
        if inherit_from:
            code_lines.append(f"class {class_name}(models.Model):")
            code_lines.append(f"    _inherit = '{inherit_from}'")
        else:
            code_lines.append(f"class {class_name}(models.Model):")
            code_lines.append(f"    _name = '{model_name}'")
            code_lines.append(f"    _description = '{model_description}'")
            
        code_lines.append("")
        
        # Add fields
        if fields:
            for field_name, field_def in fields.items():
                field_type = field_def.get('type', 'Char')
                field_args = []
                
                # String parameter
                if 'string' in field_def:
                    field_args.append(f"string='{field_def['string']}'")
                    
                # Required parameter
                if field_def.get('required'):
                    field_args.append("required=True")
                    
                # Default parameter
                if 'default' in field_def:
                    default = field_def['default']
                    if isinstance(default, str) and not default.startswith('fields.'):
                        field_args.append(f"default='{default}'")
                    else:
                        field_args.append(f"default={default}")
                        
                # Comodel for relational fields
                if 'comodel' in field_def:
                    field_args.append(f"comodel_name='{field_def['comodel']}'")
                    
                # Help text
                if 'help' in field_def:
                    field_args.append(f"help='{field_def['help']}'")
                    
                # Generate field line
                args_str = ", ".join(field_args)
                code_lines.append(f"    {field_name} = fields.{field_type}({args_str})")
                
        else:
            # Add a sample field if none provided
            code_lines.append("    name = fields.Char(string='Name', required=True)")
            
        # Add empty line at end
        code_lines.append("")
        
        # Write the file
        content = '\n'.join(code_lines)
        result = write_module_file(ctx, module_name, file_path, content)
        
        if result['success']:
            # Update models/__init__.py
            init_path = os.path.join('models', '__init__.py')
            init_result = update_init_file(ctx, module_name, init_path, filename[:-3])
            
            return {
                "success": True,
                "model_name": model_name,
                "file_path": file_path,
                "class_name": class_name,
                "message": f"Model {model_name} created successfully"
            }
        else:
            return result
            
    except Exception as e:
        logger.error(f"Error creating model file: {str(e)}")
        return {
            "success": False,
            "error": str(e)
        }


@mcp.tool(description="Create or update an XML view file in an Odoo module")
def create_view_file(
    ctx: Context,
    module_name: str,
    model_name: str,
    view_types: List[str] = None,
    filename: Optional[str] = None
) -> Dict[str, Any]:
    """
    Create XML view file with form and tree views
    
    Parameters:
        module_name: Module name
        model_name: Model name (e.g., 'my.model')
        view_types: List of view types to create (default: ['form', 'tree'])
        filename: Custom filename (optional, auto-generated if not provided)
    """
    try:
        if view_types is None:
            view_types = ['form', 'tree']
            
        # Generate filename
        if not filename:
            filename = model_name.replace('.', '_') + '_views.xml'
        file_path = os.path.join('views', filename)
        
        # Create XML structure
        root = ET.Element('odoo')
        
        # Generate view ID prefix
        view_id_prefix = model_name.replace('.', '_')
        
        # Create views
        for view_type in view_types:
            record = ET.SubElement(root, 'record', {
                'id': f'{view_id_prefix}_view_{view_type}',
                'model': 'ir.ui.view'
            })
            
            # View name
            name_field = ET.SubElement(record, 'field', {'name': 'name'})
            name_field.text = f'{model_name}.{view_type}'
            
            # Model
            model_field = ET.SubElement(record, 'field', {'name': 'model'})
            model_field.text = model_name
            
            # Architecture
            arch_field = ET.SubElement(record, 'field', {'name': 'arch', 'type': 'xml'})
            
            if view_type == 'form':
                form = ET.SubElement(arch_field, 'form')
                sheet = ET.SubElement(form, 'sheet')
                group = ET.SubElement(sheet, 'group')
                ET.SubElement(group, 'field', {'name': 'name'})
                
            elif view_type == 'tree':
                tree = ET.SubElement(arch_field, 'tree')
                ET.SubElement(tree, 'field', {'name': 'name'})
                
            elif view_type == 'search':
                search = ET.SubElement(arch_field, 'search')
                ET.SubElement(search, 'field', {'name': 'name'})
                
        # Create action
        action = ET.SubElement(root, 'record', {
            'id': f'action_{view_id_prefix}',
            'model': 'ir.actions.act_window'
        })
        
        action_name = ET.SubElement(action, 'field', {'name': 'name'})
        action_name.text = model_name.replace('.', ' ').title()
        
        action_model = ET.SubElement(action, 'field', {'name': 'res_model'})
        action_model.text = model_name
        
        action_view_mode = ET.SubElement(action, 'field', {'name': 'view_mode'})
        action_view_mode.text = ','.join(view_types)
        
        # Create menu item
        menuitem = ET.SubElement(root, 'menuitem', {
            'id': f'menu_{view_id_prefix}',
            'name': model_name.replace('.', ' ').title(),
            'action': f'action_{view_id_prefix}',
            'sequence': '10'
        })
        
        # Pretty print XML
        xml_str = prettify_xml(root)
        
        # Write file
        result = write_module_file(ctx, module_name, file_path, xml_str)
        
        if result['success']:
            # Update manifest to include the view file
            update_manifest_data_files(ctx, module_name, file_path)
            
            return {
                "success": True,
                "file_path": file_path,
                "views_created": view_types,
                "action_id": f'action_{view_id_prefix}',
                "menu_id": f'menu_{view_id_prefix}'
            }
        else:
            return result
            
    except Exception as e:
        logger.error(f"Error creating view file: {str(e)}")
        return {
            "success": False,
            "error": str(e)
        }


@mcp.tool(description="Update security access file for a model")
def update_security_file(
    ctx: Context,
    module_name: str,
    model_name: str,
    group_id: str = "base.group_user",
    permissions: Dict[str, bool] = None
) -> Dict[str, Any]:
    """
    Update ir.model.access.csv file with model permissions
    
    Parameters:
        module_name: Module name
        model_name: Model name (e.g., 'my.model')
        group_id: Security group (default: base.group_user)
        permissions: Dict with read, write, create, unlink permissions
    """
    try:
        if permissions is None:
            permissions = {
                "read": True,
                "write": True,
                "create": True,
                "unlink": True
            }
            
        file_path = os.path.join('security', 'ir.model.access.csv')
        modules_path = ctx.request_context.lifespan_context.modules_path
        full_path = os.path.join(modules_path, module_name, file_path)
        
        # Read existing file
        existing_lines = []
        if os.path.exists(full_path):
            with open(full_path, 'r') as f:
                existing_lines = f.readlines()
                
        # Check if model already has an entry
        access_id = f"access_{model_name.replace('.', '_')}"
        model_found = False
        
        for i, line in enumerate(existing_lines):
            if line.startswith(access_id):
                model_found = True
                # Update existing line
                existing_lines[i] = f"{access_id},access_{model_name.replace('.', '_')},model_{model_name.replace('.', '_')},{group_id},{int(permissions.get('read', True))},{int(permissions.get('write', True))},{int(permissions.get('create', True))},{int(permissions.get('unlink', True))}\n"
                break
                
        if not model_found:
            # Add new line
            if not existing_lines or not existing_lines[0].startswith('id,'):
                existing_lines.insert(0, "id,name,model_id:id,group_id:id,perm_read,perm_write,perm_create,perm_unlink\n")
            existing_lines.append(f"{access_id},access_{model_name.replace('.', '_')},model_{model_name.replace('.', '_')},{group_id},{int(permissions.get('read', True))},{int(permissions.get('write', True))},{int(permissions.get('create', True))},{int(permissions.get('unlink', True))}\n")
            
        # Write updated file
        content = ''.join(existing_lines)
        result = write_module_file(ctx, module_name, file_path, content)
        
        if result['success']:
            return {
                "success": True,
                "access_id": access_id,
                "model": model_name,
                "permissions": permissions,
                "message": f"Security access updated for model {model_name}"
            }
        else:
            return result
            
    except Exception as e:
        logger.error(f"Error updating security file: {str(e)}")
        return {
            "success": False,
            "error": str(e)
        }


@mcp.tool(description="Delete a file from an Odoo module")
def delete_module_file(
    ctx: Context,
    module_name: str,
    file_path: str,
    create_backup: bool = True
) -> Dict[str, Any]:
    """
    Delete a file from a module
    
    Parameters:
        module_name: Module name
        file_path: Path to file relative to module root
        create_backup: Whether to create a backup before deletion
    """
    modules_path = ctx.request_context.lifespan_context.modules_path
    full_path = os.path.join(modules_path, module_name, file_path)
    
    try:
        # Security check
        real_path = os.path.realpath(full_path)
        module_real_path = os.path.realpath(os.path.join(modules_path, module_name))
        
        if not real_path.startswith(module_real_path):
            return {
                "success": False,
                "error": "Invalid file path"
            }
            
        if not os.path.exists(full_path):
            return {
                "success": False,
                "error": f"File not found: {file_path}"
            }
            
        # Create backup if requested
        backup_path = None
        if create_backup:
            backup_path = full_path + '.deleted.' + datetime.now().strftime('%Y%m%d_%H%M%S')
            os.rename(full_path, backup_path)
            logger.info(f"Created backup at {backup_path}")
        else:
            os.remove(full_path)
            
        logger.info(f"Successfully deleted file {file_path} from module {module_name}")
        
        return {
            "success": True,
            "module": module_name,
            "file_path": file_path,
            "backup_path": backup_path,
            "message": f"File {file_path} deleted successfully"
        }
        
    except Exception as e:
        logger.error(f"Error deleting file {file_path} from module {module_name}: {str(e)}")
        return {
            "success": False,
            "error": str(e)
        }


@mcp.tool(description="Search for text patterns across all module files")
def search_in_modules(
    ctx: Context,
    pattern: str,
    module_name: Optional[str] = None,
    file_extensions: Optional[List[str]] = None,
    case_sensitive: bool = False,
    regex: bool = False
) -> Dict[str, Any]:
    """
    Search for text patterns in module files
    
    Parameters:
        pattern: Text pattern to search for
        module_name: Specific module to search in (None for all modules)
        file_extensions: List of file extensions to search (e.g., ['.py', '.xml'])
        case_sensitive: Whether search is case sensitive
        regex: Whether pattern is a regular expression
    """
    modules_path = ctx.request_context.lifespan_context.modules_path
    
    try:
        results = []
        
        # Determine which modules to search
        if module_name:
            search_modules = [module_name]
        else:
            search_modules = [d for d in os.listdir(modules_path) 
                            if os.path.isdir(os.path.join(modules_path, d))]
            
        # Default file extensions
        if not file_extensions:
            file_extensions = ['.py', '.xml', '.csv', '.js', '.scss', '.yml', '.yaml']
            
        # Compile regex if needed
        if regex:
            pattern_obj = re.compile(pattern, 0 if case_sensitive else re.IGNORECASE)
        else:
            if not case_sensitive:
                pattern = pattern.lower()
                
        for module in search_modules:
            module_path = os.path.join(modules_path, module)
            
            for root, dirs, files in os.walk(module_path):
                # Skip __pycache__
                dirs[:] = [d for d in dirs if d != '__pycache__']
                
                for filename in files:
                    # Check file extension
                    if not any(filename.endswith(ext) for ext in file_extensions):
                        continue
                        
                    file_path = os.path.join(root, filename)
                    relative_path = os.path.relpath(file_path, modules_path)
                    
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            content = f.read()
                            lines = content.splitlines()
                            
                        # Search in file
                        matches = []
                        for line_num, line in enumerate(lines, 1):
                            if regex:
                                if pattern_obj.search(line):
                                    matches.append({
                                        "line": line_num,
                                        "text": line.strip(),
                                        "match": pattern_obj.search(line).group()
                                    })
                            else:
                                search_line = line if case_sensitive else line.lower()
                                if pattern in search_line:
                                    matches.append({
                                        "line": line_num,
                                        "text": line.strip()
                                    })
                                    
                        if matches:
                            results.append({
                                "file": relative_path,
                                "module": module,
                                "matches": matches,
                                "count": len(matches)
                            })
                            
                    except Exception as e:
                        logger.warning(f"Error searching in {relative_path}: {e}")
                        
        return {
            "success": True,
            "pattern": pattern,
            "results": results,
            "total_files": len(results),
            "total_matches": sum(r['count'] for r in results)
        }
        
    except Exception as e:
        logger.error(f"Error searching in modules: {str(e)}")
        return {
            "success": False,
            "error": str(e)
        }


# @mcp.tool(description="Generate complete scaffolding for a model (model, views, security)")
# def scaffold_model(
#     ctx: Context,
#     module_name: str,
#     model_name: str,
#     model_description: str,
#     fields: Dict[str, Dict[str, Any]],
#     create_menu: bool = True,
#     menu_parent: Optional[str] = None
# ) -> Dict[str, Any]:
#     """
#     Generate complete scaffolding for a model including Python file, views, security
    
#     Parameters:
#         module_name: Module name
#         model_name: Model name (e.g., 'my.model')
#         model_description: Human-readable model name
#         fields: Field definitions
#         create_menu: Whether to create menu item
#         menu_parent: Parent menu XML ID (optional)
        
#     Example fields:
#         {
#             "name": {"type": "Char", "string": "Name", "required": True},
#             "partner_id": {"type": "Many2one", "comodel": "res.partner", "string": "Partner"},
#             "amount": {"type": "Float", "string": "Amount", "digits": [16, 2]}
#         }
#     """
#     try:
#         results = {}
        
#         # 1. Create model file
#         logger.info(f"Creating model file for {model_name}")
#         model_result = create_model_file(
#             ctx,
#             module_name,
#             model_name,
#             model_description,
#             fields=fields
#         )
#         results['model'] = model_result
        
#         if not model_result['success']:
#             return {
#                 "success": False,
#                 "error": f"Failed to create model: {model_result.get('error')}",
#             }
#     except Exception as e:
#         logger.error(f"Error scaffolding model: {str(e)}")
#         return {
#             "success": False,
#             "error": str(e)
#         }

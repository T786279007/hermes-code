"""Configuration file parser supporting INI, YAML, JSON, and TOML formats."""

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False

try:
    import tomllib
    TOML_AVAILABLE = True
except ImportError:
    try:
        import tomli as tomllib
        TOML_AVAILABLE = True
    except ImportError:
        TOML_AVAILABLE = False

import configparser


class ConfigParseError(Exception):
    """Exception raised when config parsing fails."""
    pass


class ConfigValidationError(Exception):
    """Exception raised when config validation fails."""
    pass


def parse_config(file_path: str) -> Dict[str, Any]:
    """
    Parse a configuration file and return its contents as a dictionary.

    Automatically detects the format based on file extension:
    - .json: JSON format
    - .yaml, .yml: YAML format
    - .toml: TOML format
    - .ini, .cfg: INI format

    Args:
        file_path: Path to the configuration file

    Returns:
        Dictionary containing the parsed configuration

    Raises:
        FileNotFoundError: If the file doesn't exist
        ConfigParseError: If parsing fails or format is not supported
    """
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {file_path}")

    extension = path.suffix.lower()

    try:
        if extension == '.json':
            return _parse_json(file_path)
        elif extension in ('.yaml', '.yml'):
            return _parse_yaml(file_path)
        elif extension == '.toml':
            return _parse_toml(file_path)
        elif extension in ('.ini', '.cfg'):
            return _parse_ini(file_path)
        else:
            raise ConfigParseError(
                f"Unsupported file format: {extension}. "
                f"Supported formats: .json, .yaml, .yml, .toml, .ini, .cfg"
            )
    except Exception as e:
        if isinstance(e, ConfigParseError):
            raise
        raise ConfigParseError(f"Failed to parse {file_path}: {str(e)}")


def _parse_json(file_path: str) -> Dict[str, Any]:
    """Parse a JSON configuration file."""
    with open(file_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def _parse_yaml(file_path: str) -> Dict[str, Any]:
    """Parse a YAML configuration file."""
    if not YAML_AVAILABLE:
        raise ConfigParseError(
            "YAML support requires PyYAML. Install it with: pip install PyYAML"
        )
    with open(file_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


def _parse_toml(file_path: str) -> Dict[str, Any]:
    """Parse a TOML configuration file."""
    if not TOML_AVAILABLE:
        raise ConfigParseError(
            "TOML support requires tomli (Python < 3.11) or Python 3.11+. "
            "Install with: pip install tomli"
        )
    with open(file_path, 'rb') as f:
        return tomllib.load(f)


def _parse_ini(file_path: str) -> Dict[str, Any]:
    """Parse an INI configuration file."""
    config = configparser.ConfigParser()
    config.read(file_path, encoding='utf-8')

    result = {}
    for section_name in config.sections():
        result[section_name] = dict(config[section_name])

    # Handle DEFAULT section separately if it has values
    if config.defaults():
        result['DEFAULT'] = dict(config.defaults())

    return result


def validate_config(schema: Dict[str, Any], config: Dict[str, Any]) -> bool:
    """
    Validate a configuration dictionary against a schema.

    Schema format:
    {
        'key_name': {
            'type': type | None,  # Expected type (None = any type)
            'required': bool,      # Whether the key must be present
            'default': Any,        # Default value if key is missing (optional)
            'validator': callable  # Custom validation function (optional)
        }
    }

    Args:
        schema: Schema dictionary defining expected structure
        config: Configuration dictionary to validate

    Returns:
        True if validation passes

    Raises:
        ConfigValidationError: If validation fails
    """
    errors = []

    # Apply defaults and check for required keys
    for key, spec in schema.items():
        if key not in config:
            default = spec.get('default')
            if default is not None:
                config[key] = default
            elif spec.get('required', False):
                errors.append(f"Missing required key: '{key}'")
                continue

    # Validate existing keys
    for key, value in config.items():
        if key not in schema:
            continue  # Allow extra keys not in schema

        spec = schema[key]
        expected_type = spec.get('type')

        # Type checking
        if expected_type is not None and not isinstance(value, expected_type):
            errors.append(
                f"Key '{key}' has incorrect type. "
                f"Expected {expected_type.__name__}, got {type(value).__name__}"
            )
            continue

        # Custom validator
        validator = spec.get('validator')
        if validator and not validator(value):
            errors.append(
                f"Key '{key}' failed custom validation"
            )

    if errors:
        raise ConfigValidationError(
            "Configuration validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
        )

    return True


def merge_configs(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deep merge two configuration dictionaries.

    The override dictionary takes precedence. For nested dictionaries,
    the merge is recursive. For non-dict values, override values replace
    base values.

    Args:
        base: Base configuration dictionary
        override: Override configuration dictionary

    Returns:
        Merged configuration dictionary
    """
    result = base.copy()

    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge_configs(result[key], value)
        else:
            result[key] = value

    return result


__all__ = [
    'parse_config',
    'validate_config',
    'merge_configs',
    'ConfigParseError',
    'ConfigValidationError',
]

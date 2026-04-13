"""Tests for config_parser module."""

import json
import os
import tempfile
from pathlib import Path

import pytest

from config_parser import (
    parse_config,
    validate_config,
    merge_configs,
    ConfigParseError,
    ConfigValidationError,
)


class TestJSONParsing:
    """Test JSON configuration parsing."""

    def test_parse_json_file(self):
        """Test parsing a JSON configuration file."""
        config_data = {
            "database": {
                "host": "localhost",
                "port": 5432,
                "name": "mydb"
            },
            "debug": True
        }

        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(config_data, f)
            temp_path = f.name

        try:
            result = parse_config(temp_path)
            assert result == config_data
        finally:
            os.unlink(temp_path)

    def test_parse_json_nested_structure(self):
        """Test parsing JSON with deeply nested structures."""
        config_data = {
            "server": {
                "handlers": {
                    "api": {
                        "timeout": 30,
                        "retries": 3
                    }
                }
            }
        }

        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(config_data, f)
            temp_path = f.name

        try:
            result = parse_config(temp_path)
            assert result == config_data
        finally:
            os.unlink(temp_path)


class TestINIParsing:
    """Test INI configuration parsing."""

    def test_parse_ini_file(self):
        """Test parsing an INI configuration file."""
        ini_content = """[database]
host = localhost
port = 5432
name = mydb

[server]
debug = true
workers = 4
"""

        with tempfile.NamedTemporaryFile(mode='w', suffix='.ini', delete=False) as f:
            f.write(ini_content)
            temp_path = f.name

        try:
            result = parse_config(temp_path)
            assert 'database' in result
            assert result['database']['host'] == 'localhost'
            assert result['database']['port'] == '5432'
            assert result['server']['debug'] == 'true'
        finally:
            os.unlink(temp_path)

    def test_parse_cfg_file(self):
        """Test parsing a .cfg configuration file."""
        cfg_content = """[app]
name = MyApp
version = 1.0
"""

        with tempfile.NamedTemporaryFile(mode='w', suffix='.cfg', delete=False) as f:
            f.write(cfg_content)
            temp_path = f.name

        try:
            result = parse_config(temp_path)
            assert result['app']['name'] == 'MyApp'
            assert result['app']['version'] == '1.0'
        finally:
            os.unlink(temp_path)


class TestConfigValidation:
    """Test configuration validation."""

    def test_validate_config_success(self):
        """Test successful validation."""
        schema = {
            'host': {'type': str, 'required': True},
            'port': {'type': int, 'required': True},
            'debug': {'type': bool, 'required': False}
        }

        config = {
            'host': 'localhost',
            'port': 8080,
            'debug': True
        }

        assert validate_config(schema, config) is True

    def test_validate_config_missing_required_key(self):
        """Test validation fails when required key is missing."""
        schema = {
            'host': {'type': str, 'required': True},
            'port': {'type': int, 'required': True}
        }

        config = {'host': 'localhost'}

        with pytest.raises(ConfigValidationError) as exc_info:
            validate_config(schema, config)

        assert 'Missing required key' in str(exc_info.value)

    def test_validate_config_incorrect_type(self):
        """Test validation fails when type is incorrect."""
        schema = {
            'port': {'type': int, 'required': True}
        }

        config = {'port': 'not_a_number'}

        with pytest.raises(ConfigValidationError) as exc_info:
            validate_config(schema, config)

        assert 'incorrect type' in str(exc_info.value)

    def test_validate_config_with_default(self):
        """Test validation uses default value for missing keys."""
        schema = {
            'host': {'type': str, 'required': True},
            'port': {'type': int, 'required': False, 'default': 8080}
        }

        config = {'host': 'localhost'}

        validate_config(schema, config)
        assert config['port'] == 8080

    def test_validate_config_custom_validator(self):
        """Test validation with custom validator function."""
        def positive_number(value):
            return value > 0

        schema = {
            'port': {'type': int, 'required': True, 'validator': positive_number}
        }

        config = {'port': -1}

        with pytest.raises(ConfigValidationError) as exc_info:
            validate_config(schema, config)

        assert 'failed custom validation' in str(exc_info.value)


class TestConfigMerge:
    """Test configuration merging."""

    def test_merge_simple_dicts(self):
        """Test merging simple dictionaries."""
        base = {'a': 1, 'b': 2}
        override = {'b': 3, 'c': 4}

        result = merge_configs(base, override)

        assert result == {'a': 1, 'b': 3, 'c': 4}

    def test_merge_nested_dicts(self):
        """Test deep merging of nested dictionaries."""
        base = {
            'database': {
                'host': 'localhost',
                'port': 5432
            },
            'debug': False
        }
        override = {
            'database': {
                'host': 'production.example.com'
            }
        }

        result = merge_configs(base, override)

        assert result['database']['host'] == 'production.example.com'
        assert result['database']['port'] == 5432  # Preserved from base
        assert result['debug'] is False

    def test_merge_with_new_nested_section(self):
        """Test merging when override adds new nested section."""
        base = {'app': {'name': 'MyApp'}}
        override = {'app': {'version': '1.0'}}

        result = merge_configs(base, override)

        assert result['app']['name'] == 'MyApp'
        assert result['app']['version'] == '1.0'


class TestErrorHandling:
    """Test error handling."""

    def test_parse_nonexistent_file(self):
        """Test parsing a non-existent file raises error."""
        with pytest.raises(FileNotFoundError):
            parse_config('/nonexistent/file.json')

    def test_parse_unsupported_format(self):
        """Test parsing unsupported file format raises error."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.xml', delete=False) as f:
            f.write('<config></config>')
            temp_path = f.name

        try:
            with pytest.raises(ConfigParseError) as exc_info:
                parse_config(temp_path)

            assert 'Unsupported file format' in str(exc_info.value)
        finally:
            os.unlink(temp_path)

    def test_parse_invalid_json(self):
        """Test parsing invalid JSON raises error."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write('{invalid json}')
            temp_path = f.name

        try:
            with pytest.raises(ConfigParseError):
                parse_config(temp_path)
        finally:
            os.unlink(temp_path)


class TestYAMLSupport:
    """Test YAML parsing (conditional on PyYAML availability)."""

    def test_parse_yaml_file(self):
        """Test parsing a YAML configuration file."""
        try:
            import yaml
        except ImportError:
            pytest.skip("PyYAML not installed")

        yaml_content = """
database:
  host: localhost
  port: 5432
  name: mydb
debug: true
"""

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(yaml_content)
            temp_path = f.name

        try:
            result = parse_config(temp_path)
            assert result['database']['host'] == 'localhost'
            assert result['database']['port'] == 5432
            assert result['debug'] is True
        finally:
            os.unlink(temp_path)


class TestTOMLSupport:
    """Test TOML parsing (conditional on availability)."""

    def test_parse_toml_file(self):
        """Test parsing a TOML configuration file."""
        try:
            import tomllib
        except ImportError:
            try:
                import tomli
            except ImportError:
                pytest.skip("TOML support not available")

        toml_content = """
[database]
host = "localhost"
port = 5432
name = "mydb"

[server]
debug = true
"""

        with tempfile.NamedTemporaryFile(mode='w', suffix='.toml', delete=False) as f:
            f.write(toml_content)
            temp_path = f.name

        try:
            result = parse_config(temp_path)
            assert result['database']['host'] == 'localhost'
            assert result['database']['port'] == 5432
            assert result['server']['debug'] is True
        finally:
            os.unlink(temp_path)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

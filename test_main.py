#!/usr/bin/env python3

import pytest
import io
from unittest.mock import Mock, patch, MagicMock
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from main import generate_chart_values, yaml_to_str, config_changed

# Setup YAML parser for tests
yaml = YAML()
yaml.preserve_quotes = True
yaml.indent(mapping=2, sequence=4, offset=2)

class MockPath:
    def __init__(self, path):
        self.path = path

class MockRule:
    def __init__(self, host, paths):
        self.host = host
        self.http = type('http', (), {'paths': paths})

class MockSpec:
    def __init__(self, rules, tls=False):
        self.rules = rules
        self.tls = tls

class MockMeta:
    def __init__(self, namespace):
        self.namespace = namespace

class MockIngress:
    def __init__(self, namespace, host, path, tls=False):
        self.metadata = MockMeta(namespace)
        self.spec = MockSpec([MockRule(host, [MockPath(path)])], tls)

class TestGenerateChartValues:
    """Test generate_chart_values function"""
    
    def test_empty_ingresses(self):
        """Test with empty ingresses list"""
        result = generate_chart_values([])
        
        assert 'config' in result
        assert 'x-default-endpoint' in result['config']
        assert 'endpoints' in result['config']
        assert 'storage' in result['config']
        assert len(result['config']['endpoints']) == 0
        
        # Check anchor
        anchor = result['config']['x-default-endpoint']
        assert hasattr(anchor, 'anchor')
        assert anchor.anchor.value == 'x-default-endpoint'
        assert anchor['interval'] == '1m'
        assert anchor['conditions'] == ['[STATUS] == 200']
    
    def test_single_ingress(self):
        """Test with single ingress"""
        ingresses = [
            MockIngress('test-ns', 'example.com', '/api', True)
        ]
        
        result = generate_chart_values(ingresses)
        
        assert len(result['config']['endpoints']) == 1
        endpoint = result['config']['endpoints'][0]
        
        assert endpoint['name'] == 'test-ns: https://example.com/api'
        assert endpoint['group'] == 'test-ns'
        assert endpoint['url'] == 'https://example.com/api'
        assert '<<' in endpoint  # merge key exists
    
    def test_multiple_ingresses(self):
        """Test with multiple ingresses"""
        ingresses = [
            MockIngress('ns1', 'app1.com', '/', True),
            MockIngress('ns2', 'app2.com', '/api', False)
        ]
        
        result = generate_chart_values(ingresses)
        
        assert len(result['config']['endpoints']) == 2
        
        # Check first endpoint (HTTPS)
        ep1 = result['config']['endpoints'][0]
        assert ep1['name'] == 'ns1: https://app1.com/'
        assert ep1['url'] == 'https://app1.com/'
        
        # Check second endpoint (HTTP)
        ep2 = result['config']['endpoints'][1]
        assert ep2['name'] == 'ns2: http://app2.com/api'
        assert ep2['url'] == 'http://app2.com/api'
    
    def test_existing_anchor(self):
        """Test with existing anchor in config"""
        # Create config with existing anchor
        existing_config = CommentedMap()
        existing_config['config'] = CommentedMap()
        
        # Create anchor with different name but same anchor value
        anchor_obj = CommentedMap({
            "interval": "30s",
            "conditions": ["[STATUS] == 200", "[RESPONSE_TIME] < 1000"]
        })
        anchor_obj.yaml_set_anchor('x-default-endpoint')
        existing_config['config']['my-custom-endpoint'] = anchor_obj
        
        with patch('main.GATUS_HELM_VALUES', yaml_to_str(existing_config)):
            result = generate_chart_values([])
            
            # Should find existing anchor
            assert 'my-custom-endpoint' in result['config']
            assert result['config']['my-custom-endpoint']['interval'] == '30s'
            assert len(result['config']['my-custom-endpoint']['conditions']) == 2
    
    def test_invalid_ingress(self):
        """Test with invalid ingress (no spec)"""
        ingress = MockIngress('test-ns', 'example.com', '/', True)
        ingress.spec = None  # Make it invalid
        
        result = generate_chart_values([ingress])
        
        # Should skip invalid ingress
        assert len(result['config']['endpoints']) == 0
    
    def test_ingress_without_paths(self):
        """Test ingress without paths"""
        ingress = MockIngress('test-ns', 'example.com', '/', True)
        ingress.spec.rules[0].http.paths = []  # Empty paths
        
        result = generate_chart_values([ingress])
        
        # Should skip ingress without paths
        assert len(result['config']['endpoints']) == 0

class TestYamlToStr:
    """Test yaml_to_str function"""
    
    def test_none_input(self):
        """Test with None input"""
        result = yaml_to_str(None)
        assert result is None
    
    def test_simple_dict(self):
        """Test with simple dictionary"""
        data = {'key': 'value'}
        result = yaml_to_str(data)
        
        # Should be valid YAML
        loaded = yaml.load(result)
        assert loaded['key'] == 'value'
    
    def test_with_anchors(self):
        """Test with anchors and merge keys"""
        config = CommentedMap()
        config['config'] = CommentedMap()
        
        # Create anchor
        anchor_obj = CommentedMap({
            "interval": "1m",
            "conditions": ["[STATUS] == 200"]
        })
        anchor_obj.yaml_set_anchor('x-default-endpoint')
        config['config']['x-default-endpoint'] = anchor_obj
        
        # Create endpoint with merge
        endpoint = CommentedMap()
        endpoint['<<'] = anchor_obj
        endpoint['name'] = 'test'
        config['config']['endpoints'] = [endpoint]
        
        result = yaml_to_str(config)
        
        # Should be valid YAML with merge
        loaded = yaml.load(result)
        assert 'config' in loaded
        assert 'x-default-endpoint' in loaded['config']
        assert 'endpoints' in loaded['config']
        assert len(loaded['config']['endpoints']) == 1
        assert '<<' in loaded['config']['endpoints'][0]

class TestConfigChanged:
    """Test config_changed function"""
    
    @patch('main.GATUS_TEMP_FILE', '/tmp/test-config.yaml')
    def test_new_config(self):
        """Test with new config (file doesn't exist)"""
        config = {'test': 'value'}
        
        with patch('builtins.open', side_effect=FileNotFoundError):
            result = config_changed(config)
            assert result is True
    
    @patch('main.GATUS_TEMP_FILE', '/tmp/test-config.yaml')
    def test_unchanged_config(self):
        """Test with unchanged config"""
        config = {'test': 'value'}
        config_str = yaml_to_str(config)
        
        with patch('builtins.open') as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = config_str
            result = config_changed(config)
            assert result is False
    
    @patch('main.GATUS_TEMP_FILE', '/tmp/test-config.yaml')
    def test_changed_config(self):
        """Test with changed config"""
        old_config = {'test': 'old'}
        new_config = {'test': 'new'}
        
        old_config_str = yaml_to_str(old_config)
        
        with patch('builtins.open') as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = old_config_str
            result = config_changed(new_config)
            assert result is True

if __name__ == '__main__':
    pytest.main([__file__]) 
#!/usr/bin/env python3

import os
import yaml
import subprocess
import tempfile
import logging
import signal
import sys
from kubernetes import client, config
from kubernetes.watch import Watch

# Constants
GATUS_CHART = os.getenv("GATUS_CHART", "gatus/gatus")
GATUS_CHART_REPOSITORY = os.getenv("GATUS_CHART_REPOSITORY", "https://avakarev.github.io/gatus-chart")
GATUS_CHART_VERSION = os.getenv("GATUS_CHART_VERSION", "2.5.5")
GATUS_HELM_NAMESPACE = os.getenv("GATUS_HELM_NAMESPACE", "gatus")
GATUS_HELM_RELEASE = os.getenv("GATUS_HELM_RELEASE", "gatus")
GATUS_HELM_VALUES = os.getenv("GATUS_HELM_VALUES", "")  # JSON/YAML chart values string
GATUS_TEMP_CONFIG = "/tmp/gatus-config.yaml"

# Setup logging
logging.basicConfig(level=logging.ERROR, format='%(asctime)s - %(levelname)s - %(message)s')

def get_kubernetes_client():
    """Initialize Kubernetes client"""
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    return client.CoreV1Api(), client.NetworkingV1Api()

def generate_chart_values(ingresses):
    """Generate Helm chart values based on Ingress resources"""
    # Start with base chart values
    chart_values = {
        "config": {
            "storage": {"type": "sqlite", "path": "/data/gatus.db"},
            "endpoints": []
        }
    }
    
    # Load chart values from environment variable (supports JSON or YAML)
    if GATUS_HELM_VALUES:
        try:
            env_values = yaml.safe_load(GATUS_HELM_VALUES)
            if env_values:
                chart_values.update(env_values)
        except yaml.YAMLError as e:
            logging.error(f"Invalid GATUS_HELM_VALUES YAML: {e}")
    
    # Ensure config section exists
    if "config" not in chart_values:
        chart_values["config"] = {}
    if "endpoints" not in chart_values["config"]:
        chart_values["config"]["endpoints"] = []
    
    # Add endpoints for each Ingress
    for ingress in ingresses:
        namespace = ingress.metadata.namespace
        protocol = "https" if ingress.spec.tls else "http"
        
        for rule in ingress.spec.rules:
            if not rule.http or not rule.http.paths:
                continue
                
            for path in rule.http.paths:
                if not path.path:
                    continue
                    
                chart_values["config"]["endpoints"].append({
                    "<<": "*defaults",
                    "name": f"{namespace}: {protocol}://{rule.host}{path.path}",
                    "group": namespace,
                    "url": f"{protocol}://{rule.host}{path.path}"
                })
    
    return chart_values

def deploy_gatus_chart(chart_values):
    """Deploy Gatus via Helm using chart values"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        yaml.dump(chart_values, f)
        values_file = f.name
    
    try:
        cmd = ["helm", "upgrade", "--install", GATUS_HELM_RELEASE, GATUS_CHART,
               "--version", GATUS_CHART_VERSION, "--atomic", "--namespace", GATUS_HELM_NAMESPACE, 
               "--create-namespace", "--values", values_file]
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            return True
        else:
            logging.error(f"Deployment failed: {result.stderr}")
            return False
    finally:
        os.unlink(values_file)

def ensure_helm_repo():
    """Ensure Helm repository is added and updated"""
    # Check if repo exists
    result = subprocess.run(["helm", "repo", "list"], capture_output=True, text=True)
    if result.returncode == 0 and "gatus" in result.stdout:
        pass  # Repo exists
    else:
        # Add repo
        result = subprocess.run(["helm", "repo", "add", "gatus", GATUS_CHART_REPOSITORY], 
                              capture_output=True, text=True)
        if result.returncode != 0:
            logging.error(f"Failed to add repo: {result.stderr}")
            return False
    
    # Update repos
    result = subprocess.run(["helm", "repo", "update"], capture_output=True, text=True)
    if result.returncode != 0:
        logging.error(f"Failed to update repos: {result.stderr}")
        return False
    
    return True

def config_changed(new_config):
    """Check if configuration has changed and save if needed"""
    try:
        with open(GATUS_TEMP_CONFIG, 'r') as f:
            old_config = yaml.safe_load(f)
    except (FileNotFoundError, IOError):
        old_config = None
    
    # Compare normalized YAML
    new_yaml = yaml.dump(new_config, default_flow_style=False, sort_keys=True)
    old_yaml = yaml.dump(old_config, default_flow_style=False, sort_keys=True) if old_config else None
    
    if old_yaml != new_yaml:
        # Save new config
        try:
            with open(GATUS_TEMP_CONFIG, 'w') as f:
                yaml.dump(new_config, f, default_flow_style=False)
        except IOError as e:
            logging.error(f"Failed to save config: {e}")
        return True
    
    return False

def watch_ingresses():
    """Watch for Ingress resource changes"""
    _, networking_v1 = get_kubernetes_client()
    w = Watch()
    
    if not ensure_helm_repo():
        logging.error("Failed to set up Helm repository")
        return
    
    try:
        for event in w.stream(networking_v1.list_ingress_for_all_namespaces):
            try:
                ingresses = networking_v1.list_ingress_for_all_namespaces().items
                config = generate_chart_values(ingresses)
                
                if config_changed(config):
                    if not deploy_gatus_chart(config):
                        logging.error("Deployment failed")
                    
            except Exception as e:
                logging.error(f"Error processing change: {e}")
                
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logging.error(f"Watch error: {e}")

if __name__ == "__main__":
    signal.signal(signal.SIGINT, lambda s, f: sys.exit(0))
    signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))
    watch_ingresses() 
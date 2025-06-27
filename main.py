#!/usr/bin/env python3

import os
import subprocess
import tempfile
import logging
import signal
import sys
import io
import time
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from kubernetes import client, config
from kubernetes.watch import Watch

# Constants
GATUS_CHART = os.getenv("GATUS_CHART", "gatus/gatus")
GATUS_CHART_REPOSITORY = os.getenv("GATUS_CHART_REPOSITORY", "https://avakarev.github.io/gatus-chart")
GATUS_CHART_VERSION = os.getenv("GATUS_CHART_VERSION", "2.5.5")
GATUS_HELM_NAMESPACE = os.getenv("GATUS_HELM_NAMESPACE", "gatus")
GATUS_HELM_RELEASE = os.getenv("GATUS_HELM_RELEASE", "gatus")
GATUS_HELM_VALUES = os.getenv("GATUS_HELM_VALUES", "")  # JSON/YAML
GATUS_DB_FILE = os.getenv("GATUS_DB_FILE", "/srv/gatus.db")
GATUS_TEMP_FILE = os.getenv("GATUS_TEMP_FILE", "/tmp/gatus-config.tmp.yaml")
DEBOUNCE_DELAY = float(os.getenv("DEBOUNCE_DELAY", "1.0"))  # seconds

PROTECTED_CONFIG_KEYS = ["endpoints", "storage"]

logging.basicConfig(level=logging.ERROR, format='%(asctime)s %(levelname)s: %(message)s')

yaml = YAML()

def get_kubernetes_client():
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    return client.NetworkingV1Api()

def yaml_to_str(data):
    if data is None:
        return None
    stream = io.StringIO()
    yaml.dump(data, stream)
    return stream.getvalue()

def run_helm_cmd(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logging.error("Command failed: %s\nError: %s", " ".join(cmd), result.stderr.strip())
        return False
    return True

def generate_chart_values(ingresses):
    """Generate Helm chart values based on Ingress resources"""
    chart_values = {
        "config": {
            "storage": {"type": "sqlite", "path": GATUS_DB_FILE},
            "endpoints": []
        }
    }

    if GATUS_HELM_VALUES:
        try:
            env_values = yaml.load(GATUS_HELM_VALUES)
            if env_values:
                for key, value in env_values.items():
                    if key == "config" and isinstance(value, dict):
                        if "config" not in chart_values:
                            chart_values["config"] = {}
                        for k, v in value.items():
                            if k not in PROTECTED_CONFIG_KEYS:
                                chart_values["config"][k] = v
                    else:
                        chart_values[key] = value
        except Exception as e:
            logging.error("Invalid GATUS_HELM_VALUES YAML: %s", e)

    if "config" not in chart_values:
        chart_values["config"] = {}
    if "endpoints" not in chart_values["config"]:
        chart_values["config"]["endpoints"] = []

    if "x-default-endpoint" not in chart_values["config"]:
        defaults = CommentedMap({
            "interval": "1m",
            "conditions": ["[STATUS] == 200"]
        })
        defaults.yaml_set_anchor('x-default-endpoint')
        chart_values["config"]["x-default-endpoint"] = defaults

    for ingress in ingresses:
        if not ingress.spec:
            continue
        namespace = ingress.metadata.namespace
        protocol = "https" if ingress.spec.tls else "http"
        for rule in ingress.spec.rules:
            if not rule.http or not rule.http.paths:
                continue
            for path in rule.http.paths:
                if not path.path:
                    continue
                chart_values["config"]["endpoints"].append({
                    "<<": "*x-default-endpoint",
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
        cmd = [
            "helm", "upgrade", "--install", GATUS_HELM_RELEASE, GATUS_CHART,
            "--version", GATUS_CHART_VERSION, "--atomic", "--namespace", GATUS_HELM_NAMESPACE,
            "--create-namespace", "--values", values_file
        ]
        success = run_helm_cmd(cmd)
        if not success:
            logging.error("Gatus deployment failed")
        return success
    finally:
        os.unlink(values_file)

def ensure_helm_repo():
    """Ensure Helm repository is added and updated"""
    result = subprocess.run(["helm", "repo", "list"], capture_output=True, text=True)
    if result.returncode != 0:
        logging.error("Failed to list Helm repos: %s", result.stderr.strip())
        return False
    
    if "gatus" not in result.stdout:
        result_add = subprocess.run(["helm", "repo", "add", "gatus", GATUS_CHART_REPOSITORY], capture_output=True, text=True)
        if result_add.returncode != 0:
            logging.error("Failed to add repo: %s", result_add.stderr.strip())
            return False
    
    result_update = subprocess.run(["helm", "repo", "update"], capture_output=True, text=True)
    if result_update.returncode != 0:
        logging.error("Failed to update repos: %s", result_update.stderr.strip())
        return False
    
    return True

def config_changed(new_config):
    """Check if configuration has changed and save if needed"""
    try:
        with open(GATUS_TEMP_FILE, 'r') as f:
            old_config = yaml.load(f)
    except (FileNotFoundError, IOError):
        old_config = None

    new_yaml = yaml_to_str(new_config)
    old_yaml = yaml_to_str(old_config) if old_config else None

    if old_yaml != new_yaml:
        try:
            with open(GATUS_TEMP_FILE, 'w') as f:
                yaml.dump(new_config, f)
        except IOError as e:
            logging.error("Failed to save config: %s", e)
        return True
    return False

def watch_ingresses():
    """Watch for Ingress resource changes and update Gatus configuration"""
    networking_v1 = get_kubernetes_client()
    w = Watch()

    if not ensure_helm_repo():
        logging.error("Failed to set up Helm repository")
        return

    deploying = False
    pending = False

    def do_deploy(config):
        nonlocal deploying, pending
        deploying = True
        
        try:
            while True:
                if config_changed(config):
                    if not deploy_gatus_chart(config):
                        logging.error("Deployment failed")
                        break
                
                if pending:
                    pending = False
                    # Small delay to debounce rapid changes
                    time.sleep(DEBOUNCE_DELAY)
                    try:
                        ingresses = networking_v1.list_ingress_for_all_namespaces().items
                        config = generate_chart_values(ingresses)
                    except Exception as e:
                        logging.error("Failed to fetch Ingress resources: %s", e)
                        break
                    continue
                break
        except Exception as e:
            logging.error("Error during deployment: %s", e)
        finally:
            deploying = False

    try:
        # Watch for Ingress changes and update Gatus config
        for _ in w.stream(networking_v1.list_ingress_for_all_namespaces):
            try:
                ingresses = networking_v1.list_ingress_for_all_namespaces().items
                chart_config = generate_chart_values(ingresses)
                
                if not deploying:
                    do_deploy(chart_config)
                else:
                    pending = True
            except Exception as e:
                logging.error("Error processing change: %s", e)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logging.error("Watch error: %s", e)

def exit_gracefully(signum, frame):
    sys.exit(0)

signal.signal(signal.SIGINT, exit_gracefully)
signal.signal(signal.SIGTERM, exit_gracefully)

if __name__ == "__main__":
    watch_ingresses() 
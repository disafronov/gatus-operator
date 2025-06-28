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
GATUS_CHART = os.getenv("GATUS_CHART", "gatus")
GATUS_CHART_REPOSITORY = os.getenv("GATUS_CHART_REPOSITORY", "https://twin.github.io/helm-charts")
GATUS_CHART_VERSION = os.getenv("GATUS_CHART_VERSION", "1.3.0")
GATUS_HELM_NAMESPACE = os.getenv("GATUS_HELM_NAMESPACE", "gatus")
GATUS_HELM_RELEASE = os.getenv("GATUS_HELM_RELEASE", "gatus")
GATUS_HELM_VALUES = os.getenv("GATUS_HELM_VALUES", "")  # JSON/YAML
GATUS_DB_FILE = os.getenv("GATUS_DB_FILE", "/data/gatus.db")
GATUS_TEMP_FILE = os.getenv("GATUS_TEMP_FILE", "/tmp/gatus-config.tmp.yaml")
DEBOUNCE_DELAY = float(os.getenv("DEBOUNCE_DELAY", "1.0"))  # seconds

PROTECTED_CONFIG_KEYS = ["endpoints", "storage"]

logging.basicConfig(level=logging.ERROR, format='%(asctime)s %(levelname)s: %(message)s')

yaml = YAML()
yaml.preserve_quotes = True
yaml.indent(mapping=2, sequence=4, offset=2)

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
    # Ensure we preserve anchors, aliases, and comments
    yaml.dump(data, stream, default_flow_style=False, width=float("inf"))
    return stream.getvalue()

def run_helm_cmd(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logging.error("Command failed: %s\nError: %s", " ".join(cmd), result.stderr.strip())
        return False
    return True

def generate_chart_values(ingresses):
    """Generate Helm chart values based on Ingress resources"""
    chart_values = CommentedMap()

    # 1. Load user configuration, ignoring storage and endpoints sections
    if GATUS_HELM_VALUES.strip():
        try:
            env_values = yaml.load(GATUS_HELM_VALUES)
            if env_values:
                for key, value in env_values.items():
                    if key == "config" and isinstance(value, dict):
                        if "config" not in chart_values:
                            chart_values["config"] = CommentedMap()
                        for k, v in value.items():
                            if k not in PROTECTED_CONFIG_KEYS:
                                chart_values["config"][k] = v
                    else:
                        chart_values[key] = value
        except Exception as e:
            logging.error("Invalid GATUS_HELM_VALUES YAML: %s", e)

    # Ensure config section exists
    if "config" not in chart_values:
        chart_values["config"] = CommentedMap()

    # 2. Check if x-default-endpoint anchor exists in user config, create if not
    has_default_anchor = False
    if GATUS_HELM_VALUES.strip():
        try:
            # Check if the YAML string contains the anchor
            if "&x-default-endpoint" in GATUS_HELM_VALUES:
                has_default_anchor = True
        except Exception:
            pass

    if not has_default_anchor:
        defaults = CommentedMap({
            "interval": "1m",
            "conditions": ["[STATUS] == 200"]
        })
        defaults.yaml_set_anchor('x-default-endpoint')
        chart_values["config"]["x-default-endpoint"] = defaults

    # 3. Add storage and endpoints sections
    chart_values["config"]["storage"] = {"type": "sqlite", "path": GATUS_DB_FILE}
    chart_values["config"]["endpoints"] = []

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
        # Ensure we preserve anchors, aliases, and comments
        yaml.dump(chart_values, f, default_flow_style=False, width=float("inf"))
        values_file = f.name

    try:
        cmd = [
            "helm", "upgrade", "--install", GATUS_HELM_RELEASE, f"gatus/{GATUS_CHART}",
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
    # If no repos exist, result.returncode might be non-zero, but that's OK
    # We just need to check if "gatus" is in the output (even if empty)
    
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
                # Ensure we preserve anchors, aliases, and comments
                yaml.dump(new_config, f, default_flow_style=False, width=float("inf"))
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
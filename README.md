# gatus-operator

Kubernetes operator that automatically configures and deploys Gatus monitoring based on Ingress resources.

## Features

- Watches for Ingress resource changes across all namespaces
- Automatically generates Gatus configuration from Ingress rules
- Deploys Gatus via Helm with atomic updates
- Supports both TLS and non-TLS endpoints
- Graceful shutdown handling
- Comprehensive error handling
- Configuration change detection to avoid unnecessary deployments
- Configurable via environment variables
- Minimal logging (only errors)

## Requirements

- Python 3.8+
- Helm 3.x
- Kubernetes cluster access

## Installation

```bash
# Clone the repository
git clone <repository-url>
cd gatus-operator

# Install dependencies
pip install -e .
```

## Configuration

### Environment Variables

The operator can be configured using the following environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `GATUS_RELEASE_NAME` | `gatus` | Helm release name for Gatus |
| `GATUS_CHART` | `gatus/gatus` | Helm chart name |
| `GATUS_CHART_VERSION` | `2.5.5` | Helm chart version |
| `GATUS_HELM_REPO_URL` | `https://avakarev.github.io/gatus-chart` | Helm repository URL |
| `GATUS_CONFIG_FILE` | `/tmp/gatus-config.yaml` | Local config file path |
| `GATUS_CHART_CONFIG` | `` | JSON/YAML Helm chart values string |

### Gatus Configuration

Set `GATUS_CONFIG` with your configuration (supports both JSON and YAML formats):

```bash
# YAML format
export GATUS_CONFIG='
security:
  basic:
    username: admin
    password: password
ui:
  title: "My Gatus Dashboard"
'

# JSON format
export GATUS_CONFIG='{"security":{"basic":{"username":"admin","password":"password"}},"ui":{"title":"My Gatus Dashboard"}}'
```

### Helm Chart Configuration

Set `GATUS_CHART_CONFIG` with your Helm chart values (supports both JSON and YAML formats):

```bash
# YAML format
export GATUS_CHART_CONFIG='
image:
  tag: v4.3.2
persistence:
  enabled: true
  size: 1Gi
service:
  type: LoadBalancer
config:
  security:
    basic:
      username: admin
      password: password
  ui:
    title: "My Gatus Dashboard"
'

# JSON format
export GATUS_CHART_CONFIG='{"image":{"tag":"v4.3.2"},"persistence":{"enabled":true,"size":"1Gi"},"config":{"security":{"basic":{"username":"admin","password":"password"}},"ui":{"title":"My Gatus Dashboard"}}}'
```

## Usage

### Local Development

```bash
# Basic usage with defaults
python main.py

# With custom configuration
export GATUS_RELEASE_NAME="my-gatus"
export GATUS_CHART_VERSION="2.6.0"
export GATUS_CHART_CONFIG='{"image":{"tag":"v4.3.2"},"config":{"ui":{"title":"Custom Dashboard"}}}'
python main.py
```

### In Kubernetes Cluster

```bash
# Deploy as a Pod or Deployment
kubectl apply -f deployment.yaml
```

Example Deployment with environment variables:
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: gatus-operator
spec:
  replicas: 1
  selector:
    matchLabels:
      app: gatus-operator
  template:
    metadata:
      labels:
        app: gatus-operator
    spec:
      containers:
      - name: operator
        image: gatus-operator:latest
        env:
        - name: GATUS_RELEASE_NAME
          value: "gatus"
        - name: GATUS_CHART_VERSION
          value: "2.5.5"
        - name: GATUS_CHART_CONFIG
          value: |
            image:
              tag: v4.3.2
            persistence:
              enabled: true
              size: 1Gi
            config:
              security:
                basic:
                  username: admin
                  password: password
              ui:
                title: "Cluster Monitoring"
```

## How It Works

1. The operator watches for Ingress resource changes across all namespaces
2. When an Ingress is created, updated, or deleted, it:
   - Fetches all Ingress resources
   - Generates Helm chart values with endpoints for each Ingress path
   - Compares with previous configuration
   - Deploys Gatus via Helm if configuration changed

## Endpoint Generation

For each Ingress rule and path, the operator creates a Gatus endpoint:
- **Name**: `{namespace}: {protocol}://{host}{path}`
- **Group**: Namespace name
- **URL**: Full URL with protocol, host, and path
- **Protocol**: HTTPS if TLS is configured, HTTP otherwise

The operator safely handles incomplete Ingress resources by skipping rules without HTTP paths.

## Logging

The operator uses minimal logging:
- **ERROR**: Critical errors that prevent operation
- **INFO**: Configuration application (when DEBUG/INFO level enabled)

Normal operations are logged to stdout, which is automatically collected by Kubernetes.

## Troubleshooting

### Common Issues

1. **Helm not found**: Ensure Helm is installed and in PATH
2. **Kubernetes access denied**: Check RBAC permissions
3. **Invalid GATUS_CHART_CONFIG**: Check JSON/YAML syntax in environment variable
4. **Deployment fails**: Check Helm chart compatibility and cluster resources

### Debug Mode

To enable debug logging, modify the logging level in `main.py`:
```python
logging.basicConfig(level=logging.DEBUG, ...)
```

## License

[License information]
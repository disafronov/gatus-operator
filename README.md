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
- Automatic namespace creation

## Requirements

- Python 3.8+
- UV (Python package manager)
- Helm 3.x
- Kubernetes cluster access

## Installation

```bash
git clone <repository-url>
cd gatus-operator
uv sync
```

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GATUS_CHART` | `gatus/gatus` | Helm chart name |
| `GATUS_CHART_REPOSITORY` | `https://avakarev.github.io/gatus-chart` | Helm repository URL |
| `GATUS_CHART_VERSION` | `2.5.5` | Helm chart version |
| `GATUS_HELM_NAMESPACE` | `gatus` | Kubernetes namespace for Gatus deployment |
| `GATUS_HELM_RELEASE` | `gatus` | Helm release name for Gatus |
| `GATUS_HELM_VALUES` | `` | JSON/YAML Helm chart values string |

### Helm Chart Configuration

Set `GATUS_HELM_VALUES` with your Helm chart values (supports both JSON and YAML formats):

```bash
# YAML format
export GATUS_HELM_VALUES='
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
    title: "My Gatus Dashboard"
'
```

## Usage

### Local Development

```bash
# Basic usage with defaults
uv run main.py

# With custom configuration
export GATUS_HELM_RELEASE="my-gatus"
export GATUS_CHART_VERSION="2.6.0"
export GATUS_HELM_VALUES='{"image":{"tag":"v4.3.2"},"config":{"ui":{"title":"Custom Dashboard"}}}'
uv run main.py
```

### In Kubernetes Cluster

Deploy as a Pod or Deployment with proper RBAC permissions:

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
      serviceAccountName: gatus-operator
      containers:
      - name: operator
        image: gatus-operator:latest
        env:
        - name: GATUS_HELM_RELEASE
          value: "gatus"
        - name: GATUS_CHART_VERSION
          value: "2.5.5"
        - name: GATUS_HELM_NAMESPACE
          value: "gatus"
        - name: GATUS_HELM_VALUES
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
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: gatus-operator
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: gatus-operator
rules:
- apiGroups: ["networking.k8s.io"]
  resources: ["ingresses"]
  verbs: ["get", "list", "watch"]
- apiGroups: [""]
  resources: ["namespaces"]
  verbs: ["get", "list"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: gatus-operator
subjects:
- kind: ServiceAccount
  name: gatus-operator
  namespace: default
roleRef:
  kind: ClusterRole
  name: gatus-operator
  apiGroup: rbac.authorization.k8s.io
```

## How It Works

1. The operator watches for Ingress resource changes across all namespaces
2. When an Ingress is created, updated, or deleted, it:
   - Fetches all Ingress resources
   - Generates Helm chart values with endpoints for each Ingress path
   - Compares with previous configuration
   - Deploys Gatus via Helm if configuration changed
   - Creates namespace automatically if it doesn't exist

## Endpoint Generation

For each Ingress rule and path, the operator creates a Gatus endpoint:
- Name: `{namespace}: {protocol}://{host}{path}`
- Group: Namespace name
- URL: Full URL with protocol, host, and path
- Protocol: HTTPS if TLS is configured, HTTP otherwise

The operator safely handles incomplete Ingress resources by skipping rules without HTTP paths.

## Logging

The operator uses minimal logging - only ERROR level for critical issues. Normal operations are logged to stdout, which is automatically collected by Kubernetes.

## Troubleshooting

### Common Issues

1. **Helm not found**: Ensure Helm is installed and in PATH
2. **Kubernetes access denied**: Check RBAC permissions
3. **Invalid GATUS_HELM_VALUES**: Check JSON/YAML syntax in environment variable
4. **Deployment fails**: Check Helm chart compatibility and cluster resources

#!/usr/bin/env bash
# Deploy Watchtower into this team's namespace at http://$APP_HOST/app.
# Code is mounted from a ConfigMap; credentials come from a Secret.
# ./deploy.sh --dry-run prints manifests with secret values redacted.
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"

DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    *)
      echo "unknown argument: $arg" >&2
      exit 2
      ;;
  esac
done

if [[ -f /config/kubeconfig ]]; then
  export KUBECONFIG=/config/kubeconfig
else
  mapfile -t K8S_CONFIGS < <(find /config -maxdepth 1 -type f -name '*-k8s.yaml' | sort)
  if [[ ${#K8S_CONFIGS[@]} -ge 1 ]]; then
    export KUBECONFIG="${K8S_CONFIGS[0]}"
  elif [[ "$DRY_RUN" -eq 0 ]]; then
    echo "no kubeconfig at /config/kubeconfig or /config/*-k8s.yaml" >&2
    exit 1
  fi
fi

mapfile -t TEAM_CONFIGS < <(find /config -maxdepth 1 -type f -name '*.config' | sort)
if [[ ${#TEAM_CONFIGS[@]} -ne 1 ]]; then
  echo "expected exactly one /config/*.config" >&2
  exit 1
fi
set -a
# shellcheck disable=SC1090
source "${TEAM_CONFIGS[0]}"
set +a

NS="${USERNAME:?USERNAME is not set}"
APP_NAME=alert-builder
APP_PORT=8080
APP_HOST="${INGRESS_URL:?INGRESS_URL is not set}"
APP_HOST="${APP_HOST#http://}"
APP_HOST="${APP_HOST#https://}"
APP_HOST="${APP_HOST%%/*}"

if [[ -n "${WANDB_PROJECT_PATH:-}" ]]; then
  WP="$WANDB_PROJECT_PATH"
elif [[ -n "${WANDB_TEAM:-}" && -n "${WANDB_PROJECT:-}" ]]; then
  WP="${WANDB_TEAM}/${WANDB_PROJECT}"
else
  WP=""
fi

CODE_FILES=(
  main.py
  config.py
  vss_client.py
  fixtures.py
  llm.py
  rules.py
  judge.py
  store.py
  index.html
  app.js
  styles.css
  requirements.txt
)

FROM_FILE=()
PRESENT=()
for name in "${CODE_FILES[@]}"; do
  if [[ -f "$name" ]]; then
    FROM_FILE+=(--from-file="$name")
    PRESENT+=("$name")
  else
    echo "warning: skipping missing $name" >&2
  fi
done
if [[ ${#PRESENT[@]} -eq 0 ]]; then
  echo "no application files to put in the ConfigMap" >&2
  exit 1
fi

SECRET_KEYS=(VSS_URL VSS_USERNAME VSS_PASSWORD WANDB_API_KEY WANDB_PROJECT_PATH)
# The public INGRESS_URL host does not resolve inside the cluster; pods must use the backend Service.
SECRET_ARGS=(
  --from-literal="VSS_URL=${VSS_IN_CLUSTER_URL:-http://video-backend-service:8000}"
  --from-literal="VSS_USERNAME=${USERNAME}"
  --from-literal="VSS_PASSWORD=${PASSWORD:-}"
  --from-literal="WANDB_API_KEY=${WANDB_API_KEY:-}"
  --from-literal="WANDB_PROJECT_PATH=${WP}"
)
if [[ -n "${ALERT_WEBHOOK_URL:-}" ]]; then
  SECRET_KEYS+=(ALERT_WEBHOOK_URL)
  SECRET_ARGS+=(--from-literal="ALERT_WEBHOOK_URL=${ALERT_WEBHOOK_URL}")
fi
if [[ -n "${LLM_MODEL:-}" ]]; then
  SECRET_KEYS+=(LLM_MODEL)
  SECRET_ARGS+=(--from-literal="LLM_MODEL=${LLM_MODEL}")
fi

render_workload() {
  cat <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ${APP_NAME}
  labels:
    app: ${APP_NAME}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: ${APP_NAME}
  template:
    metadata:
      labels:
        app: ${APP_NAME}
    spec:
      containers:
      - name: app
        image: python:3.12-slim
        imagePullPolicy: IfNotPresent
        ports:
        - containerPort: ${APP_PORT}
        envFrom:
        - secretRef:
            name: ${APP_NAME}-secrets
        env:
        - name: PORT
          value: "${APP_PORT}"
        - name: DATA_DIR
          value: "/tmp/alert-builder"
        - name: PYTHONUNBUFFERED
          value: "1"
        volumeMounts:
        - name: code
          mountPath: /code
        workingDir: /code
        command: ["bash", "-c"]
        args:
        - "pip install --no-cache-dir -q -r requirements.txt || true; exec python main.py"
        readinessProbe:
          httpGet:
            path: /health
            port: ${APP_PORT}
          initialDelaySeconds: 10
          periodSeconds: 10
          failureThreshold: 18
      volumes:
      - name: code
        configMap:
          name: ${APP_NAME}-code
---
apiVersion: v1
kind: Service
metadata:
  name: ${APP_NAME}
  labels:
    app: ${APP_NAME}
spec:
  selector:
    app: ${APP_NAME}
  ports:
  - name: http
    port: 80
    targetPort: ${APP_PORT}
  type: ClusterIP
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: ${APP_NAME}
  labels:
    app: ${APP_NAME}
  annotations:
    nginx.ingress.kubernetes.io/rewrite-target: /\$2
    nginx.ingress.kubernetes.io/use-regex: "true"
    nginx.ingress.kubernetes.io/proxy-buffering: "off"
    nginx.ingress.kubernetes.io/proxy-read-timeout: "300"
    nginx.ingress.kubernetes.io/proxy-send-timeout: "300"
spec:
  ingressClassName: nginx
  rules:
  - host: ${APP_HOST}
    http:
      paths:
      - path: /app(/|$)(.*)
        pathType: ImplementationSpecific
        backend:
          service:
            name: ${APP_NAME}
            port:
              number: 80
EOF
}

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "# namespace: ${NS}"
  echo "# ConfigMap ${APP_NAME}-code"
  echo "apiVersion: v1"
  echo "kind: ConfigMap"
  echo "metadata:"
  echo "  name: ${APP_NAME}-code"
  echo "  labels:"
  echo "    app: ${APP_NAME}"
  echo "data:"
  for name in "${PRESENT[@]}"; do
    echo "  ${name}: <file contents omitted>"
  done
  echo "---"
  echo "# Secret ${APP_NAME}-secrets (values redacted)"
  echo "apiVersion: v1"
  echo "kind: Secret"
  echo "metadata:"
  echo "  name: ${APP_NAME}-secrets"
  echo "  labels:"
  echo "    app: ${APP_NAME}"
  echo "type: Opaque"
  echo "stringData:"
  for key in "${SECRET_KEYS[@]}"; do
    echo "  ${key}: \"***\""
  done
  echo "---"
  render_workload
  echo "# dry-run: nothing applied"
  echo "http://${APP_HOST}/app"
  exit 0
fi

kubectl -n "$NS" create configmap "${APP_NAME}-code" \
  "${FROM_FILE[@]}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n "$NS" create secret generic "${APP_NAME}-secrets" \
  "${SECRET_ARGS[@]}" \
  --dry-run=client -o yaml | kubectl apply -f -

render_workload | kubectl -n "$NS" apply -f -

kubectl -n "$NS" rollout restart "deploy/${APP_NAME}"
kubectl -n "$NS" rollout status "deploy/${APP_NAME}" --timeout=180s
echo "http://${APP_HOST}/app"

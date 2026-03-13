{{/*
Expand the name of the chart.
*/}}
{{- define "llm-proxy.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
Truncates at 63 chars because some Kubernetes name fields are limited.
*/}}
{{- define "llm-proxy.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart label value.
*/}}
{{- define "llm-proxy.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels.
*/}}
{{- define "llm-proxy.labels" -}}
helm.sh/chart: {{ include "llm-proxy.chart" . }}
{{ include "llm-proxy.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels.
*/}}
{{- define "llm-proxy.selectorLabels" -}}
app.kubernetes.io/name: {{ include "llm-proxy.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Service account name.
*/}}
{{- define "llm-proxy.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "llm-proxy.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Secret name for API keys — either the one we create or an existing one supplied by the user.
*/}}
{{- define "llm-proxy.secretName" -}}
{{- if .Values.secrets.existingSecret }}
{{- .Values.secrets.existingSecret }}
{{- else }}
{{- include "llm-proxy.fullname" . }}
{{- end }}
{{- end }}

{{/*
Secret name for the proxy master key — separate from API keys so it is
never set in plain text via values and survives a helm uninstall.
*/}}
{{- define "llm-proxy.masterKeySecretName" -}}
{{- if .Values.secrets.existingMasterKeySecret }}
{{- .Values.secrets.existingMasterKeySecret }}
{{- else }}
{{- printf "%s-master-key" (include "llm-proxy.fullname" .) }}
{{- end }}
{{- end }}

{{/*
PostgreSQL host (bundled subchart or external).
*/}}
{{- define "llm-proxy.postgresHost" -}}
{{- printf "%s-postgresql" .Release.Name }}
{{- end }}

{{/*
Redis master host (bundled subchart).
*/}}
{{- define "llm-proxy.redisHost" -}}
{{- printf "%s-redis-master" .Release.Name }}
{{- end }}

{{/*
Chroma PVC name.
*/}}
{{- define "llm-proxy.chromaPvcName" -}}
{{- printf "%s-chroma" (include "llm-proxy.fullname" .) }}
{{- end }}

{{/*
Knowledge-base PVC name.
*/}}
{{- define "llm-proxy.kbPvcName" -}}
{{- printf "%s-kb" (include "llm-proxy.fullname" .) }}
{{- end }}

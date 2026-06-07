{{/*
helm/fuehrungszeugnis/templates/_helpers.tpl
*/}}

{{- define "fuehrungszeugnis.name" -}}
{{- .Chart.Name | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "fuehrungszeugnis.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{- define "fuehrungszeugnis.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" | quote }}
app.kubernetes.io/name: {{ include "fuehrungszeugnis.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "fuehrungszeugnis.selectorLabels" -}}
app.kubernetes.io/name: {{ include "fuehrungszeugnis.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "fuehrungszeugnis.serviceAccountName" -}}
{{- include "fuehrungszeugnis.fullname" . }}
{{- end }}

{{/* Resolve DB password from secrets.dbPassword */}}
{{- define "fuehrungszeugnis.dbPassword" -}}
{{- .Values.secrets.dbPassword }}
{{- end }}

{{/* DB host: the in-cluster postgresql service */}}
{{- define "fuehrungszeugnis.dbHost" -}}
{{- printf "%s-postgresql" .Release.Name }}
{{- end }}

{{/* DB port: always 5432 */}}
{{- define "fuehrungszeugnis.dbPort" -}}
{{- "5432" }}
{{- end }}

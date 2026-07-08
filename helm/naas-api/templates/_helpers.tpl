{{- define "naas-api.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "naas-api.fullname" -}}
{{- printf "%s-%s" .Release.Name (include "naas-api.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "naas-api.labels" -}}
app.kubernetes.io/name: {{ include "naas-api.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{- define "naas-api.selectorLabels" -}}
app.kubernetes.io/name: {{ include "naas-api.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "naas-api.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "naas-api.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

"""Kubernetes client setup."""

from kubernetes import client, config
from kubernetes.dynamic import DynamicClient


class K8sClient:
    """Loads credentials and exposes the CoreV1 and dynamic clients we use."""

    def __init__(self, in_cluster=True, kubeconfig_path=None):
        if in_cluster:
            config.load_incluster_config()
        else:
            config.load_kube_config(config_file=kubeconfig_path)

        api_client = client.ApiClient()
        self.core_v1 = client.CoreV1Api(api_client)
        # Used to enumerate arbitrary (core + CRD) resources on force-delete.
        self.dynamic = DynamicClient(api_client)

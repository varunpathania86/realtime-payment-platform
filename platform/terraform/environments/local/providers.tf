provider "kubernetes" {
  config_path    = pathexpand(var.kube_config_path)
  config_context = var.kube_context
}

provider "helm" {
  kubernetes = {
    config_path    = pathexpand(var.kube_config_path)
    config_context = var.kube_context
  }
}

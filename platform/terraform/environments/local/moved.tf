moved {
  from = random_password.kafka
  to   = random_password.kafka[0]
}
moved {
  from = random_id.kafka_cluster
  to   = random_id.kafka_cluster[0]
}
moved {
  from = tls_private_key.kafka
  to   = tls_private_key.kafka[0]
}
moved {
  from = tls_self_signed_cert.kafka
  to   = tls_self_signed_cert.kafka[0]
}
moved {
  from = kubernetes_secret_v1.kafka
  to   = kubernetes_secret_v1.kafka[0]
}
moved {
  from = kubernetes_service_v1.kafka
  to   = kubernetes_service_v1.kafka[0]
}
moved {
  from = kubernetes_stateful_set_v1.kafka
  to   = kubernetes_stateful_set_v1.kafka[0]
}
moved {
  from = kubernetes_service_v1.postgres
  to   = kubernetes_service_v1.postgres[0]
}
moved {
  from = kubernetes_stateful_set_v1.postgres
  to   = kubernetes_stateful_set_v1.postgres[0]
}

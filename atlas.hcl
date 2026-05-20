# Atlas declarative migrations (spec pick). schema.sql is the desired state.
# Apply:   atlas schema apply --env local --auto-approve
# Inspect: atlas schema inspect --env local
#
# The dev-db must have pgvector available; the pgvector image provides it.

env "local" {
  src = "file://schema.sql"
  url = "postgres://kai:kai@localhost:5433/kai_empresa?sslmode=disable"
  dev = "docker://pgvector/pgvector/pg16/dev"
}

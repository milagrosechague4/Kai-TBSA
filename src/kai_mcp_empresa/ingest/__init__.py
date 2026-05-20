"""Knowledge ingestion: read source documents → chunk → embed → upsert into the
tenant's knowledge base. Filesystem/markdown reader today; connectors plug into
the same Document interface later."""

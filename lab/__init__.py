"""
lab/ — AgentClaw Communication Lab.

Isoliertes Subpaket zum Testen von A2A/M2M Protokollen.
- Eigene Mock-Agenten (kein LLM)
- Eigener In-Memory State (keine DB-Writes)
- ID-Prefix "lab:" für Agenten, "lab_t_" für Tasks — niemals mit echten verwechselbar
- Eigene Routes /lab und /api/lab/*
"""

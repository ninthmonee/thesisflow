"""A restored store cannot silently reuse ambient local authentication."""

POLICY_FILE = "recovery-credential-isolation.json"


def requires_explicit_model_binding(database):
    return (database.path.parent / POLICY_FILE).exists()

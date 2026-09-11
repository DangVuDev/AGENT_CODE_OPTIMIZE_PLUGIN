package optimizer.bootstrap

import rego.v1

# Fail closed until organization-owned policies are reviewed and versioned.
default allow := false

decision := {
    "allow": allow,
    "policy_version": "bootstrap-v1",
    "reasons": ["business policies are not implemented"],
}


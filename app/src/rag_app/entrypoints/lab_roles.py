"""Fixed synthetic principals, not a production identity provider.

Keep the existing Aurora resource owner to preserve corpus and receipt ACLs.
Authenticated principal and legacy corpus owner are deliberately distinct.
"""
from .domain import Identity

PROFILES = {
    'ana': {'role': 'client', 'tenant': 'demo-a'},
    'bruno': {'role': 'operator', 'tenant': 'demo-a'},
}


def resource_identity(claims, required_role=None):
    profile = PROFILES.get(claims.get('sub'))
    if not profile or any(claims.get(k) != v for k, v in profile.items()):
        raise ValueError('INVALID_PROFILE')
    if required_role and profile['role'] != required_role:
        raise PermissionError('ROLE_FORBIDDEN')
    return Identity(profile['tenant'], 'demo-user')

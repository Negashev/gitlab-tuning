import os
from tasks import gl, get_ldap_owner_with_users

print(f'run admin_group_clean')

ldap_group_owner, ldap_group_users = get_ldap_owner_with_users(os.getenv("LDAP_ADMIN_GROUP", "Admin").upper())
ldap_emails = []
for ldap_user in ldap_group_users:
    try:
        email = ldap_user[1]['mail'][0].decode().lower()
    except Exception as e:
        print(e)
        continue
    ldap_emails.append(email)
gitlab_users = gl.users.list(as_list=False, per_page=100)
for user in gitlab_users:
    if user.is_admin:
        print(f"check {user.name}")
        if user.email not in ldap_emails:
            print(f"{user.email} remove admin roles")
            user.is_admin = False
            user.save()

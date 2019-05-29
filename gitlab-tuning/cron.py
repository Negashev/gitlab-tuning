from tasks import gl, CRON_AVATARS_PER_PAGE, gitlab_sync_avatars

print(f'run gitlab_sync_avatars_prepare')
users = gl.users.list(as_list=False, per_page=CRON_AVATARS_PER_PAGE, active=True)
for i in range(1, users.total_pages+1):
    gitlab_sync_avatars.send(i)
print(f'total_pages {users.total_pages}/{CRON_AVATARS_PER_PAGE}')

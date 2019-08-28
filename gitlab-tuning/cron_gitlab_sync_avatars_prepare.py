import time
from tasks import gl, CRON_AVATARS_PER_PAGE, gitlab_sync_avatars

print(f'run gitlab_sync_avatars_prepare')
users = gl.users.list(as_list=False, per_page=CRON_AVATARS_PER_PAGE, active=True)
for i in range(1, users.total_pages+1):
    condition = True
    while condition:
        try:
            gitlab_sync_avatars.send(i)
            condition = False
        except Exception as e:
            print(f'sleep 5 second, error for id {i}: {e}')
            time.sleep(5)
print(f'total_pages {users.total_pages}/{CRON_AVATARS_PER_PAGE}')

import time
from tasks import gl, CRON_PROJECTS_PER_PAGE, access_to_project

print(f'run access_to_project')
projects = gl.projects.list(as_list=False, per_page=CRON_PROJECTS_PER_PAGE, active=True)
for i in range(1, projects.total_pages+1):
    condition = True
    while condition:
        try:
            access_to_project.send(i)
            condition = False
        except Exception as e:
            print(f'sleep 5 second, error for id {i}: {e}')
            time.sleep(5)
print(f'total_pages {projects.total_pages}/{CRON_PROJECTS_PER_PAGE}')

import time
from tasks import gl, CRON_PROJECTS_PER_PAGE, access_to_project

print(f'run access_to_project')
projects = gl.projects.list(as_list=False, per_page=CRON_PROJECTS_PER_PAGE, simple=True)
for i in projects:
    condition = True
    while condition:
        try:
            print(i.id, i.name)
            access_to_project.send(i.id)
            condition = False
        except Exception as e:
            print(f'sleep 5 second, error for id {i}: {e}')
            time.sleep(5)
print(f'total_pages {projects.total_pages}/{CRON_PROJECTS_PER_PAGE}')

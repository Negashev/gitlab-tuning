# Gitlab Tuning

- auto add users with owner to new group
- send `user, commit, repo, timestamp` to statistic server 


##### Fix old DB gitlab web_hooks table

```sql
alter table web_hooks alter column push_events set default true;
alter table web_hooks alter column issues_events set default false;     
alter table web_hooks alter column merge_requests_events set default false;
alter table web_hooks alter column tag_push_events set default false;
```
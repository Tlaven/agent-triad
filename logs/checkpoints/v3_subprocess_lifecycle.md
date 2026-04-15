# Checkpoint Report: v3_subprocess_lifecycle

- Generated: 2026-04-14T21:38:03
- Duration: 0:00:39.261274
- Checkpoints: 9

---
## CP1: subprocess_spawn
`timestamp: 2026-04-14T21:37:24`

### success
```
True
```

### port_file_path
```
logs\executor.port
```

### port_file_exists
```
True
```

### port_file_content
```
62878
```

### base_url
```
http://127.0.0.1:62878
```

### is_running
```
True
```

### subprocess_pid
```
24500
```

### subprocess_returncode
```
None
```

### subprocess_stdout
```
INFO:     Started server process [2308]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     127.0.0.1:62884 - "GET /health HTTP/1.1" 200 OK

```

---
## CP2: health_check
`timestamp: 2026-04-14T21:37:27`

### http_status_code
```
200
```

### response_body
```
{
  "status": "ok"
}
```

### response_headers
```
{
  "date": "Tue, 14 Apr 2026 13:37:27 GMT",
  "server": "uvicorn",
  "content-length": "15",
  "content-type": "application/json"
}
```

---
## CP3: task_dispatch
`timestamp: 2026-04-14T21:37:27`

### http_status_code
```
200
```

### response_body
```
{
  "plan_id": "plan_checkpoint_test",
  "status": "accepted"
}
```

---
## CP4: immediate_result_after_dispatch
`timestamp: 2026-04-14T21:37:28`

### http_status_code
```
200
```

### response_body
```
{
  "status": "completed",
  "updated_plan_json": "{\"plan_id\": \"plan_checkpoint_test\", \"version\": 1, \"goal\": \"checkpoint test goal\", \"steps\": [{\"step_id\": \"step_1\", \"intent\": \"test step\", \"expected_output\": \"ok\", \"status\": \"pending\"}]}",
  "summary": "Mock executor completed successfully",
  "snapshot_json": ""
}
```

### status_field
```
completed
```

### summary_field
```
Mock executor completed successfully
```

---
## CP5: completion_poll
`timestamp: 2026-04-14T21:37:28`

### final_status
```
completed
```

### final_summary
```
Mock executor completed successfully
```

### updated_plan_json_present
```
True
```

### full_response_body
```
{
  "status": "completed",
  "updated_plan_json": "{\"plan_id\": \"plan_checkpoint_test\", \"version\": 1, \"goal\": \"checkpoint test goal\", \"steps\": [{\"step_id\": \"step_1\", \"intent\": \"test step\", \"expected_output\": \"ok\", \"status\": \"pending\"}]}",
  "summary": "Mock executor completed successfully",
  "snapshot_json": ""
}
```

---
## CP6: status_cleanup_after_completion
`timestamp: 2026-04-14T21:37:28`

### GET /status code
```
404
```

### GET /status body
```
{"detail":"Plan plan_checkpoint_test not found"}
```

### GET /result code
```
200
```

### GET /result body
```
{
  "status": "completed",
  "updated_plan_json": "{\"plan_id\": \"plan_checkpoint_test\", \"version\": 1, \"goal\": \"checkpoint test goal\", \"steps\": [{\"step_id\": \"step_1\", \"intent\": \"test step\", \"expected_output\": \"ok\", \"status\": \"pending\"}]}",
  "summary": "Mock executor completed successfully",
  "snapshot_json": ""
}
```

---
## CP7: process_stop
`timestamp: 2026-04-14T21:37:28`

### stop_success
```
True
```

### is_running_after_stop
```
False
```

### port_file_exists_after_stop
```
True
```

---
## CP8: recovery_reuse
`timestamp: 2026-04-14T21:37:38`

### first_start_base_url
```
http://127.0.0.1:53194
```

### first_start_port_file
```
53194
```

### first_start_is_running
```
True
```

### recovery_base_url
```
http://127.0.0.1:53194
```

### recovery_port_file
```
53194
```

### recovery_is_running
```
True
```

### base_url_unchanged
```
True
```

---
## CP9: duplicate_dispatch_409
`timestamp: 2026-04-14T21:37:51`

### first_dispatch_status
```
200
```

### first_dispatch_body
```
{
  "plan_id": "plan_dup_test",
  "status": "accepted"
}
```

### duplicate_dispatch_status
```
409
```

### duplicate_dispatch_body
```
{
  "detail": "Plan plan_dup_test already running"
}
```

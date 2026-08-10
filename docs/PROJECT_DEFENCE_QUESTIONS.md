# Project Defence Drill — Questions

Use this as a mock oral defence. Answer aloud before reading the answer key. Separate explicit assessment requirements from approved architecture and implementation trade-offs.

## Product and scope

1. What problem does the Drone Mapping Service solve, and who uses it?
2. Why is it a bounded service rather than a whole construction platform?
3. What end-to-end workflow would you demonstrate?
4. What did you deliberately avoid building, and why?
5. Why server-rendered Django templates and vanilla JavaScript rather than React?
6. What is the primary browser route and what does the root route do?
7. Which features make the submission demonstrable?

## Architecture

8. Describe each runtime component and its responsibility.
9. Why is the implementation a modular monolith?
10. Why Neon/PostGIS rather than a database container?
11. Why is Redis self-hosted on the VPS?
12. Why Celery, and what belongs in its workers?
13. Why must workers scale separately from the web application?
14. Why private R2 rather than database blobs or local files?
15. How do static assets differ from survey/map/model assets?
16. Why version APIs under api/v1?
17. Why separate liveness and readiness?
18. Why does readiness check dependencies?

## Authentication and authorisation

19. Why does this service not implement passwords or real login?
20. How does browser authentication work?
21. Why an HttpOnly cookie rather than localStorage?
22. Why CSRF protection with JWT cookies?
23. Which JWT claims are required and how are they used?
24. What happens for an unknown or inactive user?
25. Explain the project scope rules for all four roles.
26. Why are frontend role checks only UX?
27. What is the public assessment-demo role selector?
28. How is that temporary exception removed after review?

## Data and workflow

29. Why use PostGIS PointField EPSG:4326 for sites?
30. Why must every project have a Project Manager?
31. Why archive projects/surveys rather than hard delete?
32. Name the survey lifecycle statuses and explain server-driven transitions.
33. Who can submit, approve, reject, and archive?
34. Why prohibit self-approval?
35. What is the difference between Approval and ApprovalHistory?
36. Why must audit records be immutable?

## Files and processing

37. Describe upload admission from browser through Celery.
38. Why stage uploads before canonical private storage?
39. How are checksum duplicates handled?
40. Why is direct multipart implemented while resumable upload is deferred?
41. How do OBJ companion assets work?
42. Why use transaction.on_commit for Celery dispatch?
43. Explain processing state, retry, and idempotency.
44. Why poll ProcessingJob in PostgreSQL rather than Redis?
45. What derived 2D and 3D outputs are produced?
46. Why are original downloads more restricted than viewer outputs?

## Viewer, API, deployment, and trade-offs

47. How are map tiles delivered privately?
48. How are 3D models delivered privately?
49. Why can authorised users view ready derived outputs before approval?
50. How are measurements validated, calculated, and authorised?
51. What do OpenAPI, Swagger, and Redoc contribute?
52. Explain the Coolify deployment order.
53. Why is Redis not publicly exposed?
54. Why do migrations use the direct Neon URL?
55. Why use a one-off migration service and exclude it from health checks?
56. Which tests were prioritised and why not run a full suite for CSS?
57. What did you defer and how would you extend the service?
58. What limitations should you disclose honestly?


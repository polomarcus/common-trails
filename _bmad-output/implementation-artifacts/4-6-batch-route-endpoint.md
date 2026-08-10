# Story 4.6: Batch Route Endpoint

Status: pending

## Story

As a frontend client loading a multi-route collection,
I want to fetch multiple routes in a single API call,
So that the collection view loads fast without N sequential requests.

## Acceptance Criteria

1. **Given** a client sends `GET /routes/batch?ids=uuid1,uuid2,uuid3`
   **When** all routes exist and are accessible (public or unlisted)
   **Then** the response is a JSON array of route objects (same schema as `GET /routes/{id}`)

2. **Given** a batch request includes a private route the user doesn't own
   **When** the response is built
   **Then** that route is omitted from the array (not an error)
   **And** the response includes only the accessible routes

3. **Given** a batch request with more than 4 IDs
   **When** the request is processed
   **Then** a 400 error is returned: "Maximum 4 routes per batch request"

4. **Given** a batch request with an invalid UUID
   **When** the request is processed
   **Then** the invalid ID is ignored and valid routes are returned

## Tasks / Subtasks

- [ ] Task 1: Add `GET /routes/batch` endpoint (AC: #1, #2, #3, #4)
  - [ ] 1.1: Parse `ids` query param (comma-separated UUIDs)
  - [ ] 1.2: Validate max 4 IDs
  - [ ] 1.3: Query all routes in one DB call (`WHERE id IN (...)`)
  - [ ] 1.4: Filter by visibility (public/unlisted, or private if owned by requester)
  - [ ] 1.5: Return list of RouteOut objects with geometry

- [ ] Task 2: Tests
  - [ ] 2.1: Test happy path (2 public routes)
  - [ ] 2.2: Test private route filtering
  - [ ] 2.3: Test max 4 limit
  - [ ] 2.4: Test invalid UUID handling

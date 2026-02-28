# Performance Review Focus

Flag significant performance issues. Ignore micro-optimisations unless
they are in hot paths or O(n²) / O(n³) algorithms on large data.

## Algorithmic Complexity
- O(n²) or worse loops where O(n log n) or O(n) is feasible
- N+1 query patterns (loop + DB call per iteration)
- Repeated expensive calls inside loops (hashing, regex compilation, I/O)
- Missing pagination on queries that could return unbounded rows

## Database
- Missing indexes for new WHERE/ORDER BY/JOIN columns
- SELECT * where specific columns suffice
- Transactions that are too broad or missing entirely
- Synchronous DB calls in async context

## Memory
- Large in-memory data structures that should be streamed
- Unnecessary data copying (large strings, buffers)
- Memory leaks: unclosed files, connections, accumulating caches

## Concurrency
- Blocking calls in async/event-loop code
- Missing connection pooling for HTTP or DB clients
- Unnecessary serialisation (global locks on hot paths)

## Caching
- Missing cache where repeated identical requests are made
- Cache invalidation bugs (stale data returned after update)

## Output
Mark performance issues as `[MEDIUM]` or `[LOW]` unless they are
severe regressions. Always suggest the fix, not just the problem.

# 3_solution

Name: Service Split Runner

Approach:
- CQRS command/query split
- RabbitMQ SLA routing (`realtime`, `fast`, `batch`) and DLQ
- Reservation/capture/release billing flow
- Full product capability baseline (tiers, request modes, model simulation)
- Standard observability and OLAP event stack

Primary RFC:
- `../0_1_rfcs/RFC-0003-3-solution-cqrs-rabbitmq-sla.md`

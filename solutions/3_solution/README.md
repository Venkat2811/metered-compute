# 3_solution

CQRS + SLA routing evolution:
- Command/query service split
- RabbitMQ tiered queues (`realtime`, `fast`, `batch`) + DLQ
- Reservation/capture/release billing state machine

Primary design doc:
- `../0_0_rfcs/RFC-0004-3-solution-cqrs-rabbitmq-sla.md`

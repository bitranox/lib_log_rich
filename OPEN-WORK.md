# Open work

Standing backlog. Edit line by line, never rewrite wholesale. When an item is finished, delete
its line; the reason it closed belongs in the commit message, CHANGELOG or git history.


- [ ] (2026-10-08) [30] FOUND: enable_graylog=True with no endpoint is accepted by init() and validate_config() and silently creates no Graylog sink | size: small - decide refuse vs warn, plus test | open: consistent between init and validate_config, so not a validate gap, but an operator who sets LOG_ENABLE_GRAYLOG=1 and forgets LOG_GRAYLOG_ENDPOINT gets no error and no shipping | next: ask the owner whether enabling Graylog without an endpoint should be refused

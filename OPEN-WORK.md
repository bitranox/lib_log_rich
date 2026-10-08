# Open work

Standing backlog. Edit line by line, never rewrite wholesale. When an item is finished, delete
its line; the reason it closed belongs in the commit message, CHANGELOG or git history.


- [ ] (2026-10-08) [30] FOUND: enable_graylog=True with no endpoint is accepted by init() and validate_config() and silently creates no Graylog sink | size: small - decide refuse vs warn, plus test | open: consistent between init and validate_config, so not a validate gap, but an operator who sets LOG_ENABLE_GRAYLOG=1 and forgets LOG_GRAYLOG_ENDPOINT gets no error and no shipping | next: ask the owner whether enabling Graylog without an endpoint should be refused
- [ ] (2026-10-08) [40] FOUND: the python-logging skill states three behaviours that the unreleased commits change: pywin32 undeclared (now the `eventlog` extra), mixed LogLevel/str console_styles keys refused (now accepted), a factory console without flush() accepted until shutdown raises AttributeError (now refused at init with TypeError) | size: 4 statements x 2 copies + lib and plugin version bumps | open: skills/python-logging/sinks-and-deployment.md:118-119,284, SKILL.md:21, configuration.md:71, runtime-and-integration.md:279-280 here, plus the coding-python-logging twin in bitranox-skills; true only once released | next: release 6.4.3, then correct those lines (citing >=6.4.3) in both copies

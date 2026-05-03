AGENT ?= $(notdir $(CURDIR))

.PHONY: test dev invoke push logs

test:
	$(MAKE) -C ../.. test-agent AGENT=$(AGENT)

dev:
	$(MAKE) -C ../.. agentcore-dev AGENT=$(AGENT)

invoke:
	$(MAKE) -C ../.. agentcore-invoke-dev AGENT=$(AGENT)

push:
	$(MAKE) -C ../.. agent-push AGENT=$(AGENT) ENV=$(ENV)

logs:
	$(MAKE) -C ../.. agent-logs AGENT=$(AGENT) ENV=$(ENV)

.PHONY: ping all files pg milvus agent ingest
INV=inventory/hosts.yml

ping:
	ansible -i $(INV) all -m ping

all:
	ansible-playbook -i $(INV) site.yml

files:
	ansible-playbook -i $(INV) -l files site.yml

pg:
	ansible-playbook -i $(INV) -l pg site.yml

milvus:
	ansible-playbook -i $(INV) -l milvus site.yml

agent:
	ansible-playbook -i $(INV) -l agent site.yml

ingest:
	ansible-playbook -i $(INV) -l ingest site.yml

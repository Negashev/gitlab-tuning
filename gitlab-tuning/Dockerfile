FROM alpine

WORKDIR /src

RUN apk add --update python3
RUN apk add --update openldap
RUN apk add --update jpeg
RUN apk add --update libmemcached

ADD requirements.txt ./

RUN apk add --no-cache --virtual .build-deps build-base python3-dev py3-pip openldap-dev jpeg-dev zlib-dev libmemcached-dev \
    && pip3 --no-cache install -r requirements.txt \
	&& apk del .build-deps \
	&& rm -rf /var/cache/apk/*

VOLUME /tmp/dramatiq-prometheus

ENV RABBITMQ_URL=amqp://guest:guest@rabbitmq:5672 \
    TOKEN=your-secret-token \
    prometheus_multiproc_dir=/tmp/dramatiq-prometheus \
    dramatiq_prom_db=/tmp/dramatiq-prometheus \
    dramatiq_prom_host=0.0.0.0 \
    LDAP_URL=ldap://company.com_by:12345 \
    LDAP_USER=Reader@company.com_by \
    LDAP_PASS=PASS \
    LDAP_BASE='DC=company,DC=com_by' \
    LDAP_GROUP_PREFIX='Group ' \
    LDAP_OBJECTCLASS_GROUP=group \
    LDAP_OBJECTCLASS_USER=user \
    GITLAB_URL='https://gitlab.company.com_by' \
    GITLAB_TOKEN=TOKEN

ADD *.py ./

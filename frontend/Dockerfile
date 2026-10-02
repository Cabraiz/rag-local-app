FROM nginx@sha256:4a73073bd557c65b759505da037898b61f1be6cbcc3c2c3aeac22d2a470c1752
COPY nginx.conf /etc/nginx/nginx.conf
COPY public /usr/share/nginx/html
USER 101:101
ENTRYPOINT ["nginx", "-g", "daemon off;"]

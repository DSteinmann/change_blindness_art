# syntax=docker/dockerfile:1.7
FROM nginx:1.27-alpine

COPY frontend/public /usr/share/nginx/html
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 8080

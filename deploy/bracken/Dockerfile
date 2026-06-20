# syntax=docker/dockerfile:1.7
FROM node:22-alpine AS build

WORKDIR /app

COPY bracken/package.json bracken/package-lock.json ./
RUN npm ci

COPY bracken/ ./

ARG VITE_RELAY_URL
ENV VITE_RELAY_URL=$VITE_RELAY_URL

RUN npm run build

# ----------------------------------------------------------------------------
FROM nginx:1.27-alpine

COPY deploy/bracken/nginx-bracken.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

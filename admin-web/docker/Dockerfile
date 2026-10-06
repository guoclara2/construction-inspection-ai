FROM node:22-alpine AS build
WORKDIR /web
COPY admin-web/package.json admin-web/package-lock.json ./
RUN npm ci
COPY admin-web/index.html admin-web/vite.config.js ./
COPY admin-web/src ./src
RUN npm run build
FROM nginx:1.28-alpine
COPY --from=build /web/dist /usr/share/nginx/html
COPY admin-web/docker/default.conf /etc/nginx/conf.d/default.conf

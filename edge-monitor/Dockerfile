# Edge Monitor (React + Vite) — live view of each EdgeRobotRuntime's computation state.
FROM node:24-alpine
WORKDIR /app

COPY edge-monitor/package.json ./
RUN npm install

COPY edge-monitor/ ./

EXPOSE 5174
# Dev server (source is bind-mounted in docker-compose for live reload).
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]

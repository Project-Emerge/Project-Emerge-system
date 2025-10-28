# Dashboard Dockerfile
FROM node:24-alpine

WORKDIR /app

# Copy package files for dependency installation
COPY package.json ./
COPY tsconfig*.json ./
COPY vite.config.ts ./

# Install dependencies
RUN npm install

# Copy dashboard source code
COPY dashboard/ ./dashboard/

# Build the production bundle
RUN npm run build

# Install a simple static file server for serving the build
RUN npm install -g serve

# Expose the port
EXPOSE 5173

# Start the production server
CMD ["serve", "-s", "dashboard/dist", "-l", "5173"]

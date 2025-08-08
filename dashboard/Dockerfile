# Dashboard Dockerfile
FROM node:22-alpine

WORKDIR /app

# Copy package files for dependency installation
COPY package.json ./
COPY tsconfig*.json ./
COPY vite.config.ts ./

# Install dependencies
RUN npm install

# Copy dashboard source code
COPY dashboard/ ./dashboard/

# Expose the port
EXPOSE 5173

# Start the development server
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]

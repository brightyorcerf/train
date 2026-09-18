FROM node:22-alpine
WORKDIR /app
# Lockfile included and `npm ci`: `npm install` from package.json alone resolves fresh minor
# versions at build time, so two builds of the same commit could ship different dependencies.
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173"]

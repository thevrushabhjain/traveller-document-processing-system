FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json ./
RUN yarn install --network-timeout 600000

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN yarn build
EXPOSE 3000
CMD ["yarn", "start:prod"]

FROM mcr.microsoft.com/playwright:v1.49.0-noble
WORKDIR /app
COPY package.json ./
RUN npm install
COPY server.js ./
EXPOSE 8700
CMD ["node", "server.js"]

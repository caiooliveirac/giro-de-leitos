FROM node:20-alpine

RUN apk add --no-cache git

WORKDIR /app

COPY package.json ./
RUN npm install --omit=dev

COPY . .

# O diretório de auth é montado como volume para persistir sessão
RUN mkdir -p /app/auth_info

CMD ["node", "index.mjs"]

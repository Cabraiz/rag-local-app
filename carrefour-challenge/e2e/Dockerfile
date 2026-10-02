FROM mcr.microsoft.com/playwright@sha256:dcc5531e97840b9b5e794f2814476b21571c5124a3fca2267d73041f56e7580e
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
WORKDIR /browser
COPY e2e/package.json e2e/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY e2e/browser.cjs ./
USER 10001:10001
CMD ["node", "browser.cjs"]

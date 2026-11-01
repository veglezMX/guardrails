FROM gcr.io/k8s-minikube/kicbase@sha256:eb4fec00e8ad70adf8e6436f195cc429825ffb85f95afcdb5d8d9deb576f3e93
COPY --chmod=0444 offline.nft /usr/local/share/evolution-offline.nft
COPY --chmod=0555 entrypoint /usr/local/bin/evolution-entrypoint
ENTRYPOINT ["/usr/local/bin/evolution-entrypoint", "/sbin/init"]

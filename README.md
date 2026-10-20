# SonyRemote Web

MVP web de um controle remoto para a TV Sony Bravia **KDL-42W655A**, usando o protocolo IRCC por Wi-Fi (o mesmo do app Video & TV SideView).

Também serve como referência de layout para o app Android, que fica no repositório [sony_remote_tv](https://github.com/marcio-morais/sony_remote_tv).

## Como rodar

Requer Python 3.8+ (só biblioteca padrão).

```bash
python server.py            # acessível pelo celular na mesma rede Wi-Fi
python server.py --local    # somente neste computador
```

Abra `http://localhost:8000`, informe o IP da TV e a chave PSK, e use **Testar conexão** antes de apertar as teclas.

Na TV: Configurações > Rede > Controle IP, com autenticação "Normal e Pre-Shared Key" e a mesma chave informada na página. Os nomes dos menus variam entre gerações.

## Por que existe um servidor local

A TV só responde em HTTP puro e não envia cabeçalhos CORS. Uma página servida por HTTPS (ou de outra origem) não consegue chamá-la direto, porque o navegador bloqueia. O `server.py` serve a página e repassa os comandos para a TV a partir do PC. Ele só aceita IPs privados da rede local como destino.

## Limitação importante sobre hospedagem

A pasta `public/` é um site estático e pode ser publicada em qualquer hospedagem (ex.: Cloudflare Pages, com diretório de saída `public` e sem comando de build), mas **uma página hospedada na internet não alcança a TV**: não existe `/api/*` lá, e o navegador bloqueia chamadas de HTTPS para `http://IP-da-TV`. Hospedada, ela serve como demonstração do layout. Para controlar a TV de verdade, use o `server.py` local ou o app Android.

## Estrutura

```
public/index.html   interface do controle (HTML, CSS e JS puros)
server.py           servidor local + ponte para a TV (/api/send e /api/probe)
```

## Próximos passos

- Fluxo de registro com PIN, caso a TV não aceite PSK (resultado do "Testar conexão" indica).
- Teclado numérico e Wake-on-LAN para ligar a TV do standby profundo.

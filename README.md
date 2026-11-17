# SonyRemote Web

Controle remoto web para TVs **Sony Bravia**, usando o protocolo IRCC por Wi-Fi (o mesmo do app Video & TV SideView). Um servidor local em Python faz a ponte entre o navegador do celular e a TV.

> **English:** a web remote for Sony Bravia TVs. A small Python server on a PC in your home network serves the page and forwards key presses to the TV (Sony IRCC over HTTP), so you can use it from any phone browser. Tested only on a **KDL-42W655A**; other Bravia models with "IP Control" should work but are untested. The native Android app lives in [sony_remote_tv](https://github.com/marcio-morais/sony_remote_tv_android).

## Compatibilidade

Funciona com TVs **Sony Bravia** que tenham "Controle IP" nas configurações de rede. Foi testado **apenas na KDL-42W655A**. TVs de outras marcas usam outros protocolos e não funcionam. Este projeto não é afiliado à Sony; "Sony" e "Bravia" são marcas da Sony Group Corporation.

O app Android nativo, que funciona sem servidor, fica em [sony_remote_tv](https://github.com/marcio-morais/sony_remote_tv_android).

## Como rodar

Requer Python 3.8+ (só biblioteca padrão).

```bash
python server.py            # acessível pelo celular na mesma rede Wi-Fi
python server.py --local    # somente neste computador
```

Abra `http://localhost:8000`. Na primeira vez a página procura a TV sozinha (botão **Buscar TV na rede**); se achar mais de uma, você escolhe na lista. Depois informe a chave PSK e use **Testar conexão** antes de apertar as teclas. Se a busca não achar, digite o IP manualmente.

A busca usa SSDP (o mesmo mecanismo do app da Sony) e, como reforço, varre a faixa /24 da sua rede procurando a descrição UPnP da TV.

Na TV: Configurações > Rede > Controle IP, com autenticação "Normal e Pre-Shared Key" e a mesma chave informada na página. Os nomes dos menus variam entre gerações.

## Por que existe um servidor local

A TV só responde em HTTP puro e não envia cabeçalhos CORS. Uma página servida por HTTPS (ou de outra origem) não consegue chamá-la direto, porque o navegador bloqueia. O `server.py` serve a página e repassa os comandos para a TV a partir do PC. Ele só aceita IPs privados da rede local como destino.

## Limitação importante sobre hospedagem

A pasta `public/` é um site estático e pode ser publicada em qualquer hospedagem (no Cloudflare Workers o `wrangler.jsonc` já aponta para ela, sem comando de build), mas **uma página hospedada na internet não alcança a TV**: não existe `/api/*` lá, e o navegador bloqueia chamadas de HTTPS para `http://IP-da-TV`. Hospedada, ela serve como demonstração do layout. Para controlar a TV de verdade, use o `server.py` local ou o [app Android](https://github.com/marcio-morais/sony_remote_tv_android).

## Estrutura

```
public/index.html   interface do controle (HTML, CSS e JS puros)
server.py           servidor local + ponte para a TV (/api/send e /api/probe)
wrangler.jsonc      configuração de deploy estático no Cloudflare Workers
```

## Ligar e desligar

Na KDL-42W655A o código de alternar liga/desliga é ignorado: a TV só obedece ao de desligar, e em standby só acorda com Wake-on-LAN. O botão Liga consulta o estado da TV, desliga se ela estiver ligada e envia o pacote Wake-on-LAN se estiver em standby (o MAC é lido da rede e guardado em `tv_macs.json`). A página também acorda a TV ao ser aberta. Outros modelos podem se comportar diferente.

## Rodar sozinho no PC

Para o controle funcionar sempre que o PC estiver ligado, crie uma tarefa de inicialização que roda `server.py`. No Windows, uma Tarefa Agendada com gatilho "Na inicialização" e o comando `python server.py` na pasta do projeto resolve; reserve um IP fixo para o PC e libere a porta 8000 no firewall (rede privada). Abra `http://IP-DO-PC:8000` no celular e use "Adicionar à tela inicial" para ter o ícone. Confie apenas em redes domésticas: qualquer aparelho na rede consegue usar o servidor.

## Modelos testados

| Modelo | Teclas | Liga/desliga | Observações |
|---|---|---|---|
| KDL-42W655A | ok | desliga por IRCC, liga por Wake-on-LAN | modelo do autor |

Testou em outro modelo? Abra uma issue com **"Relatar modelo de TV"** e a linha entra na tabela.

## Contribuir

Contribuições são bem-vindas, principalmente para outros modelos Bravia. Veja o [CONTRIBUTING.md](CONTRIBUTING.md).

## Pendências

- Os códigos de SEN, Opções, Sair, Jump, números, cores e mídia ainda precisam ser validados na TV real.
- Fluxo de registro com PIN, caso a TV não aceite PSK.

## Licença

[MIT](LICENSE)

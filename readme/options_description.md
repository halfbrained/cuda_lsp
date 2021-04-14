# Options description

* root_dir_source - LSP server root directory source:
    * 0 - parent directory of '.cuda-proj'
    * 1 - first directory in project

* send_change_on_request - send changes only before request 
	* false - changes to the documents are sent to server after edit and a short delay (default)
	* true - sent only before requests (will delay server's analysis)

* hover_dlg_max_lines - hover dialog max lines number, default is 10

* enable_mouse_hover - when 'false' - 'hover' only acessible via a command

# Debug option

* manual_didopen - if 'true' - server is not started automatically, but on first invokation 
	of command `LSP-Open-Doc`, on next - document will be opened on the server and will work as usual. 

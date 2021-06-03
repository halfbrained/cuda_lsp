# Options description

* root_dir_source - LSP server root directory source (accepts list of values for fallback):
    * 0 - parent directory of '.cuda-proj'
    * 1 - first directory in project
    * 2 - project main-file's directory

* send_change_on_request - send changes only before request
    * false - changes to the documents are sent to server after edit and a short delay (default)
    * true - sent only before requests (will delay server's analysis)

* enable_mouse_hover - when 'false' - 'hover' only accessible via a command

* hover_dlg_max_lines - hover dialog max lines number, default is 10

* hover_additional_commands - list of additional commands to show in hover dialog; possible values:
    * "definition"
    * "references"
    * "implementation"
    * "declaration"
    * "type definition"

* cudatext_in_py_env - add CudaText API to Python server

* lint_type - lint display manner; string, combination of the following characters:
    * 'd' - icons to indicate severity of lint message on the line
    * 'b' - bookmarks with icons and message details on hover (overrides 'd')
    * 'B' - same as 'b' and also highlights line backgrounds (overrides 'b' and 'd')
    * 'c' - underline message's text range

* lint_underline_style - style of underline for lint target text; possible values:
    * 0 - "solid"
    * 1 - "dotted"
    * 2 - "dashes"
    * 3 - "wave"

* enable_code_tree - fill "Code Tree" from the LSP server

* tree_types_show - which document symbols to show in the "Code Tree". Empty string for default value, or comma-separated string of symbol kinds:
    * shown by default:
        * namespace
        * class
        * method
        * constructor
        * interface
        * function
        * struct
    * hidden:
        * file
        * module
        * package
        * property
        * field
        * enum
        * variable
        * constant
        * string
        * number
        * boolean
        * array
        * object
        * key
        * null
        * enummember
        * event
        * operator
        * typeparameter


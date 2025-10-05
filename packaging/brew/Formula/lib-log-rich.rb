class LibTemplate < Formula
  include Language::Python::Virtualenv

  desc "Rich-powered logging runtime with contextual metadata and multi-sink fan-out"
  homepage "https://github.com/bitranox/lib_log_rich"
  url "https://github.com/bitranox/lib_log_rich/archive/refs/tags/v1.1.0.tar.gz"
  sha256 "b5d4b5d2308dbd94e43eb1bad6a0d300c2a51c9a1c72e8c06f6ab34b90890ccf"
  license "MIT"

  depends_on "python@3.13"

  resource "rich" do
    url "https://files.pythonhosted.org/packages/fe/75/af448d8e52bf1d8fa6a9d089ca6c07ff4453d86c65c145d0a300bb073b9b/rich-14.1.0.tar.gz"
    sha256 "e497a48b844b0320d45007cdebfeaeed8db2a4f4bcf49f15e455cfc4af11eaa8"
  end

  resource "click" do
    url "https://files.pythonhosted.org/packages/46/61/de6cd827efad202d7057d93e0fed9294b96952e188f7384832791c7b2254/click-8.3.0.tar.gz"
    sha256 "e7b8232224eba16f4ebe410c25ced9f7875cb5f3263ffc93cc3e8da705e229c4"
  end

  resource "lib_cli_exit_tools" do
    url "https://files.pythonhosted.org/packages/0b/d5/9078a95ee15b4147a4c92c764256468b9d18744413f45d6694a40476a626/lib_cli_exit_tools-1.5.0.tar.gz"
    sha256 "8fdacaa92a08e9f1e2bb8e70ba5bc3c9b4e786c866894ef9e0956f1fe8c1a6fd"
  end

  resource "rich-click" do
    url "https://files.pythonhosted.org/packages/0c/4d/e8fcbd785a93dc5d7aef38f8aa4ade1e31b0c820eb2e8ff267056eda70b1/rich_click-1.9.2.tar.gz"
    sha256 "1c4212f05561be0cac6a9c1743e1ebcd4fe1fb1e311f9f672abfada3be649db6"
  end

  resource "python-dotenv" do
    url "https://files.pythonhosted.org/packages/f6/b0/4bc07ccd3572a2f9df7e6782f52b0c6c90dcbb803ac4a167702d7d0dfe1e/python_dotenv-1.1.1.tar.gz"
    sha256 "a8a6399716257f45be6a007360200409fce5cda2661e3dec71d23dc15f6189ab"
  end

  resource "pydantic" do
    url "https://files.pythonhosted.org/packages/ae/54/ecab642b3bed45f7d5f59b38443dcb36ef50f85af192e6ece103dbfe9587/pydantic-2.11.10.tar.gz"
    sha256 "dc280f0982fbda6c38fada4e476dc0a4f3aeaf9c6ad4c28df68a666ec3c61423"
  end

  def install
    virtualenv_install_with_resources
  end

  test do
    system("python3", "-c", "import lib_log_rich as m; print(m.summary_info())")
  end
end
